using System.Data.SQLite;
using System.Globalization;
using TradingPlatform.BusinessLayer;

namespace CryptoTickExtractor;

/// <summary>
/// Variante BARRES 1 m de l'extracteur crypto — née du diagnostic KuCoin (2026-07-12) : la
/// venue ne sert AUCUN historique de trades via Quantower (~100 derniers trades sur 24 s,
/// la même limite que son REST public), mais son API de chandelles remonte des mois — c'est
/// elle que le graphique 1 m affiche. Cette stratégie télécharge ces chandelles (GetHistory,
/// Period minute, HistoryType.Last) vers SQLite :
///   bars(ts PK ms UTC ouverture, open, high, low, close, volume, quote_volume, ticks)
///   + _meta k/v + _ingested (day/YYYY-MM-DD).
/// La validation A/B reste possible : compare_ab.py agrège la voie A (trades) en chandelles
/// 1 m et compare clôtures/volumes/extrêmes aux barres de cette base (détection auto du
/// schéma bars/trades côté B). Les comptes de trades et le côté agresseur, eux, n'existent
/// pas dans une barre — critères exclus, comme pour une voie B agrégée (cas OKX).
///
/// Unités : le volume des barres peut être en CONTRATS (KuCoin : multiplier 0,001, mesuré
/// sur les ticks du 2026-07-13) ; quote_volume est stocké pour trancher barre par barre
/// (vérifié par réflexion v1.146.14 : HistoryItemBar expose QuoteAssetVolume) et le premier
/// run logge un verdict calculé (close×volume vs quote_volume).
///
/// Idempotent et prudent (gabarit NqBarsExtractorStrategy, adapté du mois au JOUR) : purge
/// d'un jour APRÈS un téléchargement non vide seulement (0 reçue = existant conservé) ;
/// jours complets marqués, jour courant jamais. Contrairement à l'extracteur de ticks, un
/// jour VIDE n'est JAMAIS marqué : les marqueurs « 0 tick » ont empoisonné les reprises
/// OKX et KuCoin — au pire on re-demande, ~1 440 barres/jour, c'est gratuit. Défaut
/// one-shot (IntervalHours=0) : des instances oubliées en collecte auto 6 h se relancent
/// seules (cause probable du C: plein du 2026-07-12).
/// </summary>
public sealed class CryptoBarsExtractorStrategy : Strategy
{
    /// <summary>Dossier data du pilier historique (dépôt Portfolio). Modifiable via « Base SQLite ».</summary>
    private const string DefaultDataDir = @"C:\Users\Moi\Desktop\Claude_Code\Portfolio\crypto\historique\data";

    [InputParameter("Symbole (ex. XBTUSDTM — la connexion du symbole fixe l'exchange)", 0)]
    public Symbol? Instrument { get; set; }

    [InputParameter("Base SQLite (vide = auto historique\\data\\<symbole>-<exchange>-<marché>-qt1m.db)", 1)]
    public string DbPath = "";

    [InputParameter("Début historique YYYY-MM-DD (cible projet : juin 2026 →)", 2)]
    public string StartDate = "2026-06-01";

    [InputParameter("Fin historique YYYY-MM-DD (vide = aujourd'hui)", 3)]
    public string EndDate = "";

    [InputParameter("Période (minutes)", 4, 1, 60, 1, 0)]
    public int PeriodMinutes = 1;

    [InputParameter("Collecte auto toutes les N heures (0 = one-shot)", 5, 0, 24, 1, 0)]
    public int IntervalHours = 0;

    private static readonly DateTime Epoch = new(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
    private System.Threading.Timer? _timer;
    private readonly object _lock = new();
    private volatile bool _busy;
    private volatile bool _stopRequested;

    public CryptoBarsExtractorStrategy() => Name = "Crypto Bars Extractor";

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
        else { this.Stop(); } // mode one-shot (défaut)
    }

    protected override void OnStop()
    {
        _stopRequested = true;
        this.LogInfo("Arrêt demandé — la passe s'interrompra à la FIN du jour en cours.");
        _timer?.Dispose();
        _timer = null;
    }

    /// <summary>Une passe de collecte, protégée contre le recouvrement (timer + démarrage).</summary>
    private void RunOnce()
    {
        if (_busy)
        { this.LogInfo("Passe précédente encore en cours — ce déclenchement est ignoré. "
                     + "Si ça persiste : redémarrer Quantower."); return; }
        lock (_lock)
        {
            _busy = true;
            try { Extract(); }
            catch (Exception ex) { this.LogError($"Extracteur barres EXCEPTION : {ex}"); }
            finally { _busy = false; }
        }
    }

    private void Extract()
    {
        var s = Instrument;
        if (s is null) { this.LogError("Aucun symbole sélectionné (choisir ex. XBTUSDTM)."); return; }
        if (s.Connection is { } cx && cx.State != ConnectionState.Connected)
        {
            this.LogError($"Connexion « {cx.Name} » non active (état : {cx.State}) — aucune barre ne "
                        + "serait servie. Connecter la venue puis relancer.");
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
        this.LogInfo($"Base : {dbPath} | symbole {s.Name} ({s.Id}) "
                   + $"| exchange {CryptoTickExtractorStrategy.ExchangeSlug(s)} "
                   + $"| marché {CryptoTickExtractorStrategy.MarketClass(s)} ({s.SymbolType}) "
                   + $"| période {PeriodMinutes} min | connexion {s.ConnectionId}");

        string cs = new SQLiteConnectionStringBuilder { DataSource = dbPath }.ToString();
        using var conn = new SQLiteConnection(cs);
        conn.Open();
        Pragmas(conn);
        EnsureSchema(conn);
        if (!CheckIdentity(conn, s)) return;
        WriteMeta(conn, s);

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

        this.LogInfo($"Fenêtre de collecte : {fromDay:yyyy-MM-dd} → {lastDay:yyyy-MM-dd} "
                   + $"({(lastDay - fromDay).Days + 1} jour(s)) en barres de {PeriodMinutes} min.");

        long grand = 0;
        DateTime? firstServedDay = null;
        (double close, double volume, double quoteVol)? sample = null;
        for (var day = fromDay; day <= lastDay; day = day.AddDays(1))
        {
            if (_stopRequested)
            { this.LogInfo($"Arrêt demandé — passe interrompue proprement avant {day:yyyy-MM-dd} (reprise sûre au prochain Start)."); break; }
            var dayEnd = day.AddDays(1);
            int rows = IngestDay(conn, s, day, dayEnd, ref sample);
            grand += rows;
            if (rows > 0 && firstServedDay is null) firstServedDay = day;
            bool complete = dayEnd <= today;
            // Marquage UNIQUEMENT si le jour a livré des barres : un marqueur « 0 » a
            // empoisonné les reprises OKX/KuCoin de l'extracteur de ticks. Un jour vide
            // sera simplement re-demandé à la prochaine passe (~1 440 barres, gratuit).
            if (complete && rows > 0)
                using (var mk = new SQLiteCommand("INSERT OR REPLACE INTO _ingested VALUES(@n,@r,@a)", conn))
                {
                    mk.Parameters.AddWithValue("@n", $"day/{day:yyyy-MM-dd}");
                    mk.Parameters.AddWithValue("@r", rows);
                    mk.Parameters.AddWithValue("@a", DateTime.UtcNow.ToString("o"));
                    mk.ExecuteNonQuery();
                }
            this.LogInfo($"{day:yyyy-MM-dd} : {rows,7} barres"
                       + (complete ? (rows > 0 ? " [marqué]" : " [vide : NON marqué, sera retenté]")
                                   : " [courant, partiel]"));
        }

        this.LogInfo($"Extraction terminée : +{grand} barres sur cette passe.");

        // --- MESURES (alimentent le comparatif méthode A / méthode B) --------------------- //
        if (firstServedDay is DateTime f)
            this.LogInfo($"MESURE profondeur : premier jour servi = {f:yyyy-MM-dd} "
                       + $"(demandé depuis {fromDay:yyyy-MM-dd}"
                       + (f > fromDay ? $" → {(f - fromDay).Days} jours vides en tête = hors profondeur)" : " : servi dès le début)"));
        else if (grand == 0 && fromDay <= today)
            this.LogInfo("MESURE profondeur : AUCUNE barre servie sur la fenêtre — profondeur dépassée ou connexion sans historique de barres.");
        if (sample is { } t)
        {
            // Verdict calculé (close×volume ≈ notionnel de la barre, VWAP ignoré → tolérance large).
            double notional = t.close * t.volume;
            string verdict = double.IsNaN(t.quoteVol) || t.quoteVol <= 0
                ? "quote_volume indisponible — trancher via le ratio de volumes A/B (compare_ab.py)"
                : Math.Abs(notional / t.quoteVol - 1) < 0.05
                    ? "close×volume ≈ quote_volume ⇒ volume en actif de base (ex. BTC)"
                    : $"close×volume/quote_volume = {notional / t.quoteVol:0.####} ⇒ volume vraisemblablement "
                    + $"en CONTRATS (multiplier ≈ {t.quoteVol / notional:0.######})";
            this.LogInfo($"MESURE unités : 1re barre close={t.close} volume={t.volume} "
                       + $"quote_volume={t.quoteVol} — {verdict}");
        }
        LogFooter(conn);
    }

    /// <summary>Télécharge un jour de barres puis REMPLACE la plage en base — uniquement si le
    /// téléchargement a rapporté quelque chose (0 reçue = on ne touche pas à l'existant).</summary>
    private int IngestDay(SQLiteConnection conn, Symbol s, DateTime dayStart, DateTime dayEnd,
                          ref (double, double, double)? sample)
    {
        var bars = new List<(long ts, double o, double h, double l, double c, double v, double qv, long n)>();
        HistoricalData? hd = null;
        try
        {
            hd = s.GetHistory(new Period(BasePeriod.Minute, PeriodMinutes), HistoryType.Last,
                              dayStart, dayEnd);
            if (hd is not null)
                foreach (var raw in hd)
                {
                    if (raw is not HistoryItemBar b) continue;
                    long ts = ToMs(b.TimeLeft);
                    if (ts < ToMs(dayStart) || ts >= ToMs(dayEnd)) continue; // stricte au jour
                    sample ??= (b.Close, b.Volume, b.QuoteAssetVolume); // mesure des unités (1re barre servie)
                    bars.Add((ts, b.Open, b.High, b.Low, b.Close, b.Volume, b.QuoteAssetVolume, b.Ticks));
                }
        }
        finally { hd?.Dispose(); }

        if (bars.Count == 0) return 0; // rien reçu -> on conserve l'existant tel quel

        using var tx = conn.BeginTransaction();
        using (var del = new SQLiteCommand("DELETE FROM bars WHERE ts >= @a AND ts < @b", conn, tx))
        {
            del.Parameters.AddWithValue("@a", ToMs(dayStart));
            del.Parameters.AddWithValue("@b", ToMs(dayEnd));
            del.ExecuteNonQuery();
        }
        using var cmd = new SQLiteCommand(
            "INSERT OR REPLACE INTO bars(ts,open,high,low,close,volume,quote_volume,ticks) "
          + "VALUES(@ts,@o,@h,@l,@c,@v,@qv,@n)", conn, tx);
        var pTs = cmd.Parameters.Add("@ts", System.Data.DbType.Int64);
        var pO = cmd.Parameters.Add("@o", System.Data.DbType.Double);
        var pH = cmd.Parameters.Add("@h", System.Data.DbType.Double);
        var pL = cmd.Parameters.Add("@l", System.Data.DbType.Double);
        var pC = cmd.Parameters.Add("@c", System.Data.DbType.Double);
        var pV = cmd.Parameters.Add("@v", System.Data.DbType.Double);
        var pQv = cmd.Parameters.Add("@qv", System.Data.DbType.Double);
        var pN = cmd.Parameters.Add("@n", System.Data.DbType.Int64);
        foreach (var (ts, o, h, l, c, v, qv, n) in bars)
        {
            pTs.Value = ts; pO.Value = o; pH.Value = h; pL.Value = l;
            pC.Value = c; pV.Value = v; pQv.Value = double.IsNaN(qv) ? (object)DBNull.Value : qv;
            pN.Value = n;
            cmd.ExecuteNonQuery();
        }
        tx.Commit();
        return bars.Count;
    }

    // --- SQLite -------------------------------------------------------------------------- //
    private static void Pragmas(SQLiteConnection c)
    {
        foreach (var p in new[] { "journal_mode=WAL", "synchronous=NORMAL", "cache_size=-65536" })
            using (var cmd = new SQLiteCommand($"PRAGMA {p}", c)) cmd.ExecuteNonQuery();
    }

    private static void EnsureSchema(SQLiteConnection c)
    {
        Exec(c, @"CREATE TABLE IF NOT EXISTS bars(
                    ts           INTEGER PRIMARY KEY,
                    open         REAL NOT NULL,
                    high         REAL NOT NULL,
                    low          REAL NOT NULL,
                    close        REAL NOT NULL,
                    volume       REAL NOT NULL,
                    quote_volume REAL,
                    ticks        INTEGER NOT NULL)");
        Exec(c, "CREATE TABLE IF NOT EXISTS _ingested(name TEXT PRIMARY KEY, rows INTEGER, at TEXT)");
        Exec(c, "CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)");
    }

    private void WriteMeta(SQLiteConnection c, Symbol s)
    {
        string exchange = CryptoTickExtractorStrategy.ExchangeSlug(s);
        var meta = new List<(string k, string v)>
        {
            ("symbol", s.Name),
            ("market", s.SymbolType.ToString().ToLowerInvariant()),
            ("exchange", exchange),
            ("connection", s.ConnectionId ?? ""),
            ("tick_size", s.TickSize.ToString(CultureInfo.InvariantCulture)),
            ("source", $"quantower-bars-{exchange}"),
            ("period_min", PeriodMinutes.ToString(CultureInfo.InvariantCulture)),
        };
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
        using var cmd = new SQLiteCommand("SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars", c);
        using var r = cmd.ExecuteReader();
        if (r.Read() && !r.IsDBNull(1))
        {
            var lo = FromMs(r.GetInt64(1));
            var hi = FromMs(r.GetInt64(2));
            this.LogInfo($"Base : {r.GetInt64(0)} barres | {lo:o} → {hi:o} "
                       + $"({(hi - lo).TotalDays:F1} jours de profondeur)");
        }
    }

    private string ResolveDbPath(Symbol s)
        => string.IsNullOrWhiteSpace(DbPath)
            // Standard du pilier, suffixe qt<période>m : BTCUSDT-kucoin-perp-qt1m.db — la
            // voie A homologue (trades) reste <…>-api.db, la voie B ticks <…>-qt.db.
            ? Path.Combine(DefaultDataDir,
                $"{CryptoTickExtractorStrategy.SymbolSlug(s)}-{CryptoTickExtractorStrategy.ExchangeSlug(s)}"
              + $"-{CryptoTickExtractorStrategy.MarketClass(s)}-qt{PeriodMinutes}m.db")
            : DbPath;

    /// <summary>Garde-fou d'identité : même logique que l'extracteur de ticks (refus d'écrire
    /// dans une base NON VIDE d'une autre venue ou d'un autre marché), sur la table bars.</summary>
    private bool CheckIdentity(SQLiteConnection c, Symbol s)
    {
        using (var any = new SQLiteCommand("SELECT EXISTS(SELECT 1 FROM bars)", c))
            if (Convert.ToInt64(any.ExecuteScalar()) == 0) return true;
        string Meta(string k)
        {
            using var cmd = new SQLiteCommand("SELECT v FROM _meta WHERE k=@k", c);
            cmd.Parameters.AddWithValue("@k", k);
            return cmd.ExecuteScalar() as string ?? "";
        }
        string have = CryptoTickExtractorStrategy.Slug(Meta("exchange"));
        string want = CryptoTickExtractorStrategy.ExchangeSlug(s);
        if (have.Length > 0 && !have.Contains(want) && !want.Contains(have))
        {
            this.LogError($"REFUS : cette base contient des barres « {have} », le symbole choisi "
                        + $"vient de « {want} ». Laisser « Base SQLite » vide (nommage auto) ou "
                        + "pointer une autre base.");
            return false;
        }
        string haveMkt = CryptoTickExtractorStrategy.Slug(Meta("market"));
        string haveClass = haveMkt.Length == 0 ? "" : (haveMkt is "swap" or "futures" or "perp" or "um" ? "perp" : "spot");
        string wantClass = CryptoTickExtractorStrategy.MarketClass(s);
        if (haveClass.Length > 0 && haveClass != wantClass)
        {
            this.LogError($"REFUS : cette base contient du {haveClass} et le symbole choisi est "
                        + $"du {wantClass} (type {s.SymbolType}). Vérifier le symbole ou pointer "
                        + "une autre base.");
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
