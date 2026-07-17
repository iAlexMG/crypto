using System.Data.SQLite;
using System.Globalization;
using TradingPlatform.BusinessLayer;

namespace CryptoTickExtractor;

/// <summary>
/// Méthode B du pilier « historique » — extracteur incrémental de ticks crypto via une
/// connexion Quantower (Binance, Bybit, OKX, … : c'est le Symbol choisi qui décide), vers
/// SQLite au schéma EXACT de la méthode A (binance_history.py / bybit_history.py) :
/// `trades(trade_id PK, ts, price, size, side)` + `_meta` k/v + `_ingested` → toute la chaîne
/// Python aval (candles.py, …) fonctionne telle quelle. Tourne DANS Quantower (la connexion
/// n'est authentifiée que là — même verrou que Rithmic, mesuré Phase 0 du projet NQ).
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
///
/// Multi-exchange (2026-07-12) : l'exchange est déduit de la connexion du symbole
/// (Symbol.Connection.VendorName, vérifié par réflexion v1.146.14) et nomme la base au
/// standard du pilier `<sym>-<exchange>-<marché>-qt.db` (marché = perp|spot), aligné sur
/// la voie A homologue `<sym>-<exchange>-<marché>-api.db` (ex. BTCUSDT-bybit-perp-api.db ↔
/// BTCUSDT-bybit-perp-qt.db) : la paire à comparer se lit dans les noms, le marché est
/// toujours visible (piège spot/perp), et aucune venue ne peut écraser la base d'une autre.
/// </summary>
public sealed class CryptoTickExtractorStrategy : Strategy
{
    /// <summary>Dossier data du pilier historique (dépôt Portfolio). Modifiable via « Base SQLite ».</summary>
    private const string DefaultDataDir = @"C:\Users\Moi\Desktop\Claude_Code\Portfolio\crypto\historique\data";

    [InputParameter("Symbole (ex. BTCUSDT — la connexion du symbole fixe l'exchange)", 0)]
    public Symbol? Instrument { get; set; }

    [InputParameter("Base SQLite (vide = auto historique\\data\\<symbole>-<exchange>-<marché>-qt.db)", 1)]
    public string DbPath = "";

    [InputParameter("Début historique YYYY-MM-DD (cible projet : juin 2026 →)", 2)]
    public string StartDate = "2026-06-01";

    [InputParameter("Fin historique YYYY-MM-DD (vide = aujourd'hui)", 3)]
    public string EndDate = "";

    [InputParameter("Collecte auto toutes les N heures (0 = one-shot)", 4, 0, 24, 1, 0)]
    public int IntervalHours = 6;

    private static readonly DateTime Epoch = new(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
    private System.Threading.Timer? _timer;
    private readonly object _lock = new();
    private volatile bool _busy;
    private volatile bool _stopRequested;

    public CryptoTickExtractorStrategy() => Name = "Crypto History Ticks";

    protected override void OnRun()
    {
        _stopRequested = false;
        RunOnce();
        if (IntervalHours > 0)
        {
            var period = TimeSpan.FromHours(IntervalHours);
            _timer = new System.Threading.Timer(_ => RunOnce(), null, period, period);
            this.LogInfo($"Collecte automatique ACTIVE : toutes les {IntervalHours} h tant que "
                       + "la stratégie tourne (Quantower ouvert + connexion du symbole active). Laisser en Working.");
        }
        else { this.Stop(); } // mode one-shot
    }

    protected override void OnStop()
    {
        // Le flag est indispensable : sans lui, la passe en cours (boucle de jours) continue
        // jusqu'au bout même après Stop — constaté sur le premier run (janvier → février…).
        _stopRequested = true;
        this.LogInfo("Arrêt demandé — la passe s'interrompra à la FIN du jour en cours. "
                   + "Le téléchargement d'historique déjà lancé côté plateforme peut, lui, "
                   + "continuer d'afficher des dates : c'est Quantower qui vide sa file, pas la stratégie.");
        _timer?.Dispose();
        _timer = null;
    }

    /// <summary>Une passe de collecte, protégée contre le recouvrement (timer + démarrage).</summary>
    private void RunOnce()
    {
        // Log obligatoire : deux fois (premier run OKX), un Start pendant qu'une passe
        // restait coincée dans GetTickHistory s'est « sauté » sans aucune trace, laissant
        // croire à une stratégie morte. Le recouvrement reste interdit, mais il se DIT.
        if (_busy)
        { this.LogInfo("Passe précédente encore en cours (téléchargement d'historique côté "
                     + "plateforme) — ce déclenchement est ignoré. Si ça persiste : redémarrer Quantower."); return; }
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
        // Connexion inactive = chaque GetTickHistory rendrait 0 tick (passe « vide » silencieuse,
        // constaté au premier essai Bybit) → refus net avec la cause.
        if (s.Connection is { } cx && cx.State != ConnectionState.Connected)
        {
            this.LogError($"Connexion « {cx.Name} » non active (état : {cx.State}) — aucun tick ne "
                        + "serait servi. Connecter la venue puis relancer.");
            return;
        }
        if (!DateTime.TryParseExact(StartDate, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var startDay))
        { this.LogError($"Début historique invalide : « {StartDate} » (attendu YYYY-MM-DD)."); return; }
        startDay = DateTime.SpecifyKind(startDay.Date, DateTimeKind.Utc);
        DateTime? endDay = null;
        if (!string.IsNullOrWhiteSpace(EndDate))
        {
            if (!DateTime.TryParseExact(EndDate.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var e))
            { this.LogError($"Fin historique invalide : « {EndDate} » (attendu YYYY-MM-DD, ou vide)."); return; }
            endDay = DateTime.SpecifyKind(e.Date, DateTimeKind.Utc);
        }

        string dbPath = ResolveDbPath(s);
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);
        this.LogInfo($"Base : {dbPath} | symbole {s.Name} ({s.Id}) | exchange {ExchangeSlug(s)} "
                   + $"| marché {MarketClass(s)} ({s.SymbolType}) | connexion {s.ConnectionId}");

        string cs = new SQLiteConnectionStringBuilder { DataSource = dbPath }.ToString();
        using var conn = new SQLiteConnection(cs);
        conn.Open();
        Pragmas(conn);
        EnsureSchema(conn);
        if (!CheckIdentity(conn, s)) return;
        WriteMeta(conn, s);

        // Jour de reprise : lendemain du dernier jour complet marqué, MAIS jamais avant
        // StartDate — relever « Début historique » doit primer sur la reprise (sinon une base
        // commencée plus tôt repartirait de son dernier jour, comme le run janvier→février).
        var today = DateTime.SpecifyKind(DateTime.UtcNow.Date, DateTimeKind.Utc);
        var lastDay = endDay is DateTime bound && bound < today ? bound : today;
        DateTime fromDay = startDay;
        if (LastIngestedDay(conn) is DateTime last)
        {
            var resume = last.AddDays(1);
            fromDay = resume > startDay ? resume : startDay;
            if (resume < startDay)
                this.LogInfo($"Reprise à {startDay:yyyy-MM-dd} (Début historique) — trou assumé depuis {last:yyyy-MM-dd}.");
        }
        if (fromDay > lastDay) { this.LogInfo($"Déjà à jour (aucun jour à collecter jusqu'à {lastDay:yyyy-MM-dd})."); LogFooter(conn); return; }

        // Annonce de fenêtre AVANT le premier GetTickHistory : sur une venue lente (OKX :
        // ~100 trades/requête côté API), le premier jour peut rester muet 30-60 min — sans
        // cette ligne, impossible de vérifier depuis le log que les réglages sont les bons.
        this.LogInfo($"Fenêtre de collecte : {fromDay:yyyy-MM-dd} → {lastDay:yyyy-MM-dd} "
                   + $"({(lastDay - fromDay).Days + 1} jour(s)) — le premier jour peut être long "
                   + "(le téléchargement ne se voit qu'à la fin du jour).");

        // Purge du reliquat non marqué (jour courant partiel des runs précédents) : c'est la
        // QUEUE de la table (rowids les plus hauts) → ré-insertion conserve l'ordre chrono.
        long fromMs = ToMs(fromDay);
        using (var del = new SQLiteCommand("DELETE FROM trades WHERE ts >= @from", conn))
        { del.Parameters.AddWithValue("@from", fromMs); int n = del.ExecuteNonQuery(); if (n > 0) this.LogInfo($"Purge reliquat : {n} ticks (≥ {fromDay:yyyy-MM-dd})"); }

        long grand = 0, buys = 0, sells = 0, unknown = 0;
        DateTime? firstServedDay = null;
        (double price, double size, double quoteVol)? sample = null;
        for (var day = fromDay; day <= lastDay; day = day.AddDays(1))
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
        {
            // Verdict CALCULÉ (v5) : l'ancien suffixe « ≈ quoteVol ⇒ actif de base » était un
            // texte figé, lu comme une conclusion au premier run KuCoin alors que les nombres
            // disaient l'inverse (size en contrats, facteur 1000).
            double notional = t.price * t.size;
            string verdict = double.IsNaN(t.quoteVol) || t.quoteVol <= 0
                ? "quoteVol indisponible — trancher via le ratio de volumes A/B (compare_ab.py)"
                : Math.Abs(notional / t.quoteVol - 1) < 0.05
                    ? "size×price ≈ quoteVol ⇒ size en actif de base (ex. BTC)"
                    : $"size×price/quoteVol = {notional / t.quoteVol:0.####} ⇒ size vraisemblablement "
                    + $"en CONTRATS (multiplier ≈ {t.quoteVol / notional:0.######})";
            this.LogInfo($"MESURE unités : 1er tick price={t.price} size={t.size} "
                       + $"quoteVol={t.quoteVol} — {verdict}");
        }
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
            ("exchange", ExchangeSlug(s)),
            ("connection", s.ConnectionId ?? ""),
            ("tick_size", s.TickSize.ToString(CultureInfo.InvariantCulture)),
            ("source", $"quantower-{ExchangeSlug(s)}"),
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
        => string.IsNullOrWhiteSpace(DbPath)
            // Standard du pilier <symbole>-<exchange>-<marché>-qt.db : le marché (perp|spot)
            // est TOUJOURS dans le nom (piège spot/perp du premier run Bybit), la voie A
            // homologue s'obtient en remplaçant qt par api.
            ? Path.Combine(DefaultDataDir, $"{SymbolSlug(s)}-{ExchangeSlug(s)}-{MarketClass(s)}-qt.db")
            : DbPath;

    /// <summary>Symbole pour le nom de fichier : premier mot du Symbol.Name (Bybit nomme le
    /// perp « BTC/USDT Perpetual » → le suffixe décoratif sortirait dans le nom), débarrassé
    /// des suffixes de marché PORTÉS PAR LE NOM (OKX nomme le perp « BTC-USDT-SWAP », sans
    /// espace : le premier mot est tout le nom → BTCUSDTSWAP au premier run OKX ; le marché a
    /// déjà sa place dans le nom de base via MarketClass) puis de tout caractère non
    /// alphanumérique (« BTC/USDT » → BTCUSDT, « BTC-USDT-SWAP » → BTCUSDT).
    /// KuCoin (préparé AVANT le premier run, à confirmer au diagnostic) : le perp linéaire
    /// se nomme <BASE>USDTM (M = margine USDT) et BTC garde son ancien code XBT côté API
    /// (XBTUSDTM) → « USDTM » perd son M final puis « XBT » → « BTC », pour retomber sur le
    /// slug commun de la voie A (BTCUSDT-kucoin-perp-api.db, cf. kucoin_history.py).</summary>
    internal static string SymbolSlug(Symbol s)  // internal : partagé avec CryptoBarsExtractorStrategy
    {
        var first = s.Name.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)
                          .FirstOrDefault() ?? s.Name;
        foreach (var suffix in new[] { "-SWAP", "-PERP", "-PERPETUAL" })
            if (first.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
            { first = first[..^suffix.Length]; break; }
        var slug = new string(first.Where(char.IsLetterOrDigit).ToArray());
        if (slug.EndsWith("USDTM", StringComparison.OrdinalIgnoreCase))
            slug = slug[..^1]; // <BASE>USDTM (KuCoin) -> <BASE>USDT
        if (slug.StartsWith("XBT", StringComparison.OrdinalIgnoreCase))
            slug = "BTC" + slug[3..]; // XBT (ancien code BTC, KuCoin API) -> BTC
        return slug.Length > 0 ? slug : "symbole";
    }

    /// <summary>Identifiant d'exchange en minuscules/alphanumérique, déduit de la CONNEXION du
    /// symbole (VendorName : « Binance », « Bybit », … — pas renommable par l'utilisateur,
    /// contrairement à Connection.Name), repli sur l'Exchange du symbole.</summary>
    internal static string ExchangeSlug(Symbol s)  // internal : partagé avec CryptoBarsExtractorStrategy
    {
        var slug = Slug(s.Connection?.VendorName ?? s.Exchange?.ExchangeName ?? "inconnu");
        // « Bybit V5 » → bybitv5 : la version du connecteur n'identifie pas la venue
        // (constaté au premier run Bybit) → suffixe v<n> retiré.
        var m = System.Text.RegularExpressions.Regex.Match(slug, @"^(.+?)v\d+$");
        if (m.Success) slug = m.Groups[1].Value;
        // Alias historiques de venue : Quantower nomme la connexion OKX « OKEx » (ancien nom)
        // → normalisé vers le slug de la voie A (BTCUSDT-okx-perp-api.db), sinon la règle
        // « qt ↔ api en changeant le suffixe » casse (constaté au premier run OKX).
        if (slug == "okex") slug = "okx";
        // « KuCoin Futures » → kucoin : le segment de marché n'identifie pas la venue
        // (il a déjà sa place via MarketClass) — préparé AVANT le premier run KuCoin.
        if (slug.EndsWith("futures") && slug.Length > "futures".Length)
            slug = slug[..^"futures".Length];
        return slug.Length > 0 ? slug : "inconnu";
    }

    internal static string Slug(string raw)  // internal : partagé avec CryptoBarsExtractorStrategy
        => new(raw.ToLowerInvariant().Where(char.IsLetterOrDigit).ToArray());

    /// <summary>« perp » pour les dérivés (le périmètre du projet), « spot » pour le reste.
    /// Sert au nommage ET au garde-fou : « BTC/USDT » (spot) et « BTCUSDT » (perp) d'une même
    /// venue donneraient sinon le même nom de fichier — constaté au premier run Bybit, où le
    /// spot a été collecté à la place du perpétuel.</summary>
    internal static string MarketClass(Symbol s)  // internal : partagé avec CryptoBarsExtractorStrategy
    {
        var st = s.SymbolType.ToString().ToLowerInvariant();
        return st is "swap" or "futures" ? "perp" : "spot";
    }

    /// <summary>Garde-fou d'identité : refuse d'écrire dans une base NON VIDE construite pour
    /// une autre venue (ex. ticks Bybit dans la base Binance validée — le scénario du premier
    /// essai Bybit, où l'ancien nommage envoyait tout vers la même base). Comparaison par
    /// slugs avec inclusion, pour tolérer les métadonnées des anciennes versions
    /// (« Binance USDT-M Futures » ⊇ « binance »). Base vide = adopte l'identité courante.</summary>
    private bool CheckIdentity(SQLiteConnection c, Symbol s)
    {
        using (var any = new SQLiteCommand("SELECT EXISTS(SELECT 1 FROM trades)", c))
            if (Convert.ToInt64(any.ExecuteScalar()) == 0) return true;
        string Meta(string k)
        {
            using var cmd = new SQLiteCommand("SELECT v FROM _meta WHERE k=@k", c);
            cmd.Parameters.AddWithValue("@k", k);
            return cmd.ExecuteScalar() as string ?? "";
        }
        string have = Slug(Meta("exchange"));
        string want = ExchangeSlug(s);
        if (have.Length > 0 && !have.Contains(want) && !want.Contains(have))
        {
            this.LogError($"REFUS : cette base contient des ticks « {have} », le symbole choisi "
                        + $"vient de « {want} ». Laisser « Base SQLite » vide (nommage auto par "
                        + "exchange) ou pointer une autre base.");
            return false;
        }
        // Même venue mais autre marché (spot vs perp) = autres prix, autres volumes → refus
        // aussi (constaté au premier run Bybit : BTC/USDT spot collecté à la place du perp).
        string haveMkt = Slug(Meta("market"));
        string haveClass = haveMkt.Length == 0 ? "" : (haveMkt is "swap" or "futures" or "perp" or "um" ? "perp" : "spot");
        string wantClass = MarketClass(s);
        if (haveClass.Length > 0 && haveClass != wantClass)
        {
            this.LogError($"REFUS : cette base contient du {haveClass} et le symbole choisi est "
                        + $"du {wantClass} (type {s.SymbolType}). Vérifier le symbole (perp = "
                        + "« BTCUSDT » type Swap ; spot = « BTC/USDT ») ou pointer une autre base.");
            return false;
        }
        return true;
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
