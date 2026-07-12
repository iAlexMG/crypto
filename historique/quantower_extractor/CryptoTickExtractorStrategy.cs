using System.Data.SQLite;
using System.Globalization;
using TradingPlatform.BusinessLayer;

namespace CryptoTickExtractor;

/// <summary>
/// Méthode B du pilier « historique » — extracteur incrémental de ticks crypto via le channel
/// Binance de Quantower, vers SQLite au schéma EXACT de la méthode A (binance_history.py) :
/// `trades(trade_id PK, ts, price, size, side)` + `_meta` k/v + `_ingested` → toute la chaîne
/// Python aval (candles.py, …) fonctionne telle quelle. Tourne DANS Quantower (la connexion
/// Binance n'est authentifiée que là — même verrou que Rithmic, mesuré Phase 0 du projet NQ).
///
/// Adapté de l'extracteur NQ/Rithmic archivé (_archive/Quantower/extractor). Différences :
/// borne de départ par date (cible projet : juin 2026 →) au lieu d'un backfill en jours, et
/// instrumentation de MESURE au premier run (profondeur réellement servie, % d'agresseur
/// inconnu, unités de size via QuoteAssetVolume) — ces mesures alimentent le comparatif
/// méthode A / méthode B du README.
///
/// Vérifié par réflexion sur la DLL v1.146.14 : HistoryItemLast n'expose PAS de TradeId
/// (comme Rithmic) → `trade_id` = rowid d'insertion, et l'incrémental repose sur le marquage
/// par jour (jours complets marqués dans `_ingested`, jour courant purgé puis ré-inséré).
/// Insertion en ordre chronologique → rowid croissant = hypothèse de candles.py.
/// </summary>
public sealed class CryptoTickExtractorStrategy : Strategy
{
    /// <summary>Dossier data du pilier historique (dépôt Portfolio). Modifiable via « Base SQLite ».</summary>
    private const string DefaultDataDir = @"C:\Users\Moi\Desktop\Claude_Code\Portfolio\crypto\historique\data";

    [InputParameter("Symbole (ex. BTCUSDT via connexion Binance)", 0)]
    public Symbol? Instrument { get; set; }

    [InputParameter("Base SQLite (vide = auto historique\\data\\<symbole>-<marché>-quantower.db)", 1)]
    public string DbPath = "";

    [InputParameter("Début historique YYYY-MM-DD (cible projet : juin 2026 →)", 2)]
    public string StartDate = "2026-06-01";

    [InputParameter("Collecte auto toutes les N heures (0 = one-shot)", 3, 0, 24, 1, 0)]
    public int IntervalHours = 6;

    private static readonly DateTime Epoch = new(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
    private System.Threading.Timer? _timer;
    private readonly object _lock = new();
    private volatile bool _busy;
    private volatile bool _stopRequested;

    public CryptoTickExtractorStrategy() => Name = "Crypto Tick Extractor (Binance)";

    protected override void OnRun()
    {
        _stopRequested = false;
        RunOnce();
        if (IntervalHours > 0)
        {
            var period = TimeSpan.FromHours(IntervalHours);
            _timer = new System.Threading.Timer(_ => RunOnce(), null, period, period);
            this.LogInfo($"Collecte automatique ACTIVE : toutes les {IntervalHours} h tant que "
                       + "la stratégie tourne (Quantower ouvert + Binance connecté). Laisser en Working.");
        }
        else { this.Stop(); } // mode one-shot
    }

    protected override void OnStop()
    {
        // Le flag est indispensable : sans lui, la passe en cours (boucle de jours) continue
        // jusqu'au bout même après Stop — constaté sur le premier run (janvier → février…).
        _stopRequested = true;
        _timer?.Dispose();
        _timer = null;
    }

    /// <summary>Une passe de collecte, protégée contre le recouvrement (timer + démarrage).</summary>
    private void RunOnce()
    {
        if (_busy) return;
        lock (_lock)
        {
            _busy = true;
            try { Extract(); }
            catch (Exception ex) { this.LogError($"Extracteur EXCEPTION : {ex}"); }
            finally { _busy = false; }
        }
    }

    private void Extract()
    {
        var s = Instrument;
        if (s is null) { this.LogError("Aucun symbole sélectionné (choisir ex. BTCUSDT)."); return; }
        if (!DateTime.TryParseExact(StartDate, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var startDay))
        { this.LogError($"Début historique invalide : « {StartDate} » (attendu YYYY-MM-DD)."); return; }
        startDay = DateTime.SpecifyKind(startDay.Date, DateTimeKind.Utc);

        string dbPath = ResolveDbPath(s);
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);
        this.LogInfo($"Base : {dbPath} | symbole {s.Name} ({s.Id}) | connexion {s.ConnectionId}");

        string cs = new SQLiteConnectionStringBuilder { DataSource = dbPath }.ToString();
        using var conn = new SQLiteConnection(cs);
        conn.Open();
        Pragmas(conn);
        EnsureSchema(conn);
        WriteMeta(conn, s);

        // Jour de reprise : lendemain du dernier jour complet marqué, MAIS jamais avant
        // StartDate — relever « Début historique » doit primer sur la reprise (sinon une base
        // commencée plus tôt repartirait de son dernier jour, comme le run janvier→février).
        var today = DateTime.SpecifyKind(DateTime.UtcNow.Date, DateTimeKind.Utc);
        DateTime fromDay = startDay;
        if (LastIngestedDay(conn) is DateTime last)
        {
            var resume = last.AddDays(1);
            fromDay = resume > startDay ? resume : startDay;
            if (resume < startDay)
                this.LogInfo($"Reprise à {startDay:yyyy-MM-dd} (Début historique) — trou assumé depuis {last:yyyy-MM-dd}.");
        }
        if (fromDay > today) { this.LogInfo("Déjà à jour (aucun jour à collecter)."); LogFooter(conn); return; }

        // Purge du reliquat non marqué (jour courant partiel des runs précédents) : c'est la
        // QUEUE de la table (rowids les plus hauts) → ré-insertion conserve l'ordre chrono.
        long fromMs = ToMs(fromDay);
        using (var del = new SQLiteCommand("DELETE FROM trades WHERE ts >= @from", conn))
        { del.Parameters.AddWithValue("@from", fromMs); int n = del.ExecuteNonQuery(); if (n > 0) this.LogInfo($"Purge reliquat : {n} ticks (≥ {fromDay:yyyy-MM-dd})"); }

        long grand = 0, buys = 0, sells = 0, unknown = 0;
        DateTime? firstServedDay = null;
        (double price, double size, double quoteVol)? sample = null;
        for (var day = fromDay; day <= today; day = day.AddDays(1))
        {
            if (_stopRequested)
            { this.LogInfo($"Arrêt demandé — passe interrompue proprement avant {day:yyyy-MM-dd} (reprise sûre au prochain Start)."); break; }
            var dayEnd = day.AddDays(1);
            int rows = IngestDay(conn, s, day, dayEnd, ref buys, ref sells, ref unknown, ref sample);
            grand += rows;
            if (rows > 0 && firstServedDay is null) firstServedDay = day;
            bool complete = dayEnd <= today; // jour entièrement passé
            if (complete)
                using (var mk = new SQLiteCommand("INSERT OR REPLACE INTO _ingested VALUES(@n,@r,@a)", conn))
                {
                    mk.Parameters.AddWithValue("@n", $"day/{day:yyyy-MM-dd}");
                    mk.Parameters.AddWithValue("@r", rows);
                    mk.Parameters.AddWithValue("@a", DateTime.UtcNow.ToString("o"));
                    mk.ExecuteNonQuery();
                }
            this.LogInfo($"{day:yyyy-MM-dd} : {rows,9} ticks{(complete ? " [marqué]" : " [courant, partiel]")}");
        }

        EnsureTsIndex(conn);
        this.LogInfo($"Extraction terminée : +{grand} ticks sur cette passe.");

        // --- MESURES (alimentent le comparatif méthode A / méthode B du README) ----------- //
        if (firstServedDay is DateTime f)
            this.LogInfo($"MESURE profondeur : premier jour servi = {f:yyyy-MM-dd} "
                       + $"(demandé depuis {fromDay:yyyy-MM-dd}"
                       + (f > fromDay ? $" → {(f - fromDay).Days} jours vides en tête = hors profondeur)" : " : servi dès le début)"));
        else if (grand == 0 && fromDay <= today)
            this.LogInfo("MESURE profondeur : AUCUN tick servi sur la fenêtre — profondeur dépassée ou connexion sans historique tick.");
        long agg = buys + sells + unknown;
        if (agg > 0)
        {
            double pct = 100.0 * unknown / agg;
            this.LogInfo($"MESURE agresseur : buy={buys} sell={sells} inconnu={unknown} ({pct:0.####} % exclus)");
            if (pct > 0.1) this.LogError("MESURE agresseur : > 0,1 % d'inconnus exclus — volume sous-estimé, à investiguer.");
        }
        if (sample is { } t)
            this.LogInfo($"MESURE unités : 1er tick price={t.price} size={t.size} quoteVol={t.quoteVol} "
                       + $"(size×price={t.price * t.size:0.####} ; ≈ quoteVol ⇒ size en actif de base, ex. BTC)");
        LogFooter(conn);
    }

    /// <summary>Télécharge et insère un jour de ticks Last, triés chronologiquement.</summary>
    private int IngestDay(SQLiteConnection conn, Symbol s, DateTime dayStart, DateTime dayEnd,
                          ref long buys, ref long sells, ref long unknown,
                          ref (double, double, double)? sample)
    {
        var ticks = new List<(long ts, double price, double size, string side)>();
        HistoricalData? hd = null;
        try
        {
            hd = s.GetTickHistory(HistoryType.Last, dayStart, dayEnd);
            foreach (var raw in hd)
            {
                if (raw is not HistoryItemLast t) continue;
                string side;
                switch (t.AggressorFlag)
                {
                    case AggressorFlag.Buy: side = "buy"; buys++; break;
                    case AggressorFlag.Sell: side = "sell"; sells++; break;
                    default: unknown++; continue; // agresseur inconnu : exclu, jamais de side vide en base
                }
                sample ??= (t.Price, t.Volume, t.QuoteAssetVolume); // mesure des unités (1er tick servi)
                ticks.Add((ToMs(t.TimeLeft), t.Price, t.Volume, side));
            }
        }
        finally { hd?.Dispose(); }

        ticks.Sort((a, b) => a.ts.CompareTo(b.ts)); // rowid croissant = chrono (hypothèse candles.py)

        // trade_id NON spécifié → rowid = max+1 : append, ordre chronologique préservé.
        using var tx = conn.BeginTransaction();
        using var cmd = new SQLiteCommand(
            "INSERT INTO trades(ts,price,size,side) VALUES(@ts,@p,@sz,@sd)", conn, tx);
        var pTs = cmd.Parameters.Add("@ts", System.Data.DbType.Int64);
        var pP = cmd.Parameters.Add("@p", System.Data.DbType.Double);
        var pSz = cmd.Parameters.Add("@sz", System.Data.DbType.Double);
        var pSd = cmd.Parameters.Add("@sd", System.Data.DbType.String);
        foreach (var (ts, price, size, side) in ticks)
        {
            pTs.Value = ts; pP.Value = price; pSz.Value = size; pSd.Value = side;
            cmd.ExecuteNonQuery();
        }
        tx.Commit();
        return ticks.Count;
    }

    // --- SQLite : schéma identique à la méthode A (binance_history.py) ------------------- //
    private static void Pragmas(SQLiteConnection c)
    {
        foreach (var p in new[] { "journal_mode=WAL", "synchronous=NORMAL", "cache_size=-262144" })
            using (var cmd = new SQLiteCommand($"PRAGMA {p}", c)) cmd.ExecuteNonQuery();
    }

    private static void EnsureSchema(SQLiteConnection c)
    {
        Exec(c, @"CREATE TABLE IF NOT EXISTS trades(
                    trade_id INTEGER PRIMARY KEY,
                    ts       INTEGER NOT NULL,
                    price    REAL    NOT NULL,
                    size     REAL    NOT NULL,
                    side     TEXT    NOT NULL)");
        Exec(c, "CREATE TABLE IF NOT EXISTS _ingested(name TEXT PRIMARY KEY, rows INTEGER, at TEXT)");
        Exec(c, "CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)");
    }

    private static void EnsureTsIndex(SQLiteConnection c)
        => Exec(c, "CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)");

    private void WriteMeta(SQLiteConnection c, Symbol s)
    {
        var meta = new List<(string k, string v)>
        {
            ("symbol", s.Name),                                        // candles.py lit cette clé
            ("market", s.SymbolType.ToString().ToLowerInvariant()),    // candles.py lit cette clé
            ("exchange", s.Exchange?.ExchangeName ?? "Binance"),
            ("connection", s.ConnectionId ?? ""),
            ("tick_size", s.TickSize.ToString(CultureInfo.InvariantCulture)),
            ("source", "quantower-binance"),
        };
        try // les perpétuels n'expirent pas : n'écrire l'échéance que si elle existe vraiment
        {
            if (s.ExpirationDate.Year > 2000)
                meta.Add(("expiration", s.ExpirationDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)));
        }
        catch { /* symbole sans échéance */ }
        foreach (var (k, v) in meta)
            using (var cmd = new SQLiteCommand("INSERT OR REPLACE INTO _meta VALUES(@k,@v)", c))
            { cmd.Parameters.AddWithValue("@k", k); cmd.Parameters.AddWithValue("@v", v); cmd.ExecuteNonQuery(); }
    }

    private DateTime? LastIngestedDay(SQLiteConnection c)
    {
        using var cmd = new SQLiteCommand(
            "SELECT name FROM _ingested WHERE name LIKE 'day/%' ORDER BY name DESC LIMIT 1", c);
        if (cmd.ExecuteScalar() is string name &&
            DateTime.TryParse(name.Substring(4), CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var d))
            return DateTime.SpecifyKind(d.Date, DateTimeKind.Utc);
        return null;
    }

    private void LogFooter(SQLiteConnection c)
    {
        using var cmd = new SQLiteCommand("SELECT COUNT(*), MIN(ts), MAX(ts) FROM trades", c);
        using var r = cmd.ExecuteReader();
        if (r.Read() && !r.IsDBNull(1))
            this.LogInfo($"Base : {r.GetInt64(0)} ticks | {FromMs(r.GetInt64(1)):o} → {FromMs(r.GetInt64(2)):o}");
    }

    private string ResolveDbPath(Symbol s)
    {
        if (!string.IsNullOrWhiteSpace(DbPath)) return DbPath;
        var safe = string.Concat(s.Name.Split(Path.GetInvalidFileNameChars()));
        // Schéma <symbole>-<marché>-<source>.db, aligné sur la méthode A (…-um-api.db) pour que
        // la paire à comparer saute aux yeux. Marché : perp/futures = um (périmètre projet,
        // USDⓈ-M ; COIN-M hors périmètre), le reste = spot.
        var st = s.SymbolType.ToString().ToLowerInvariant();
        string market = st is "swap" or "futures" ? "um" : "spot";
        return Path.Combine(DefaultDataDir, $"{safe}-{market}-quantower.db");
    }

    private static void Exec(SQLiteConnection c, string sql)
    { using var cmd = new SQLiteCommand(sql, c); cmd.ExecuteNonQuery(); }

    private static long ToMs(DateTime dt)
    {
        var utc = dt.Kind == DateTimeKind.Utc ? dt : dt.ToUniversalTime();
        return (long)(utc - Epoch).TotalMilliseconds;
    }

    private static DateTime FromMs(long ms) => Epoch.AddMilliseconds(ms);
}
