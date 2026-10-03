import os, requests, random, datetime, time, threading, sqlite3, logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup

# ============================================================
# 1. CONFIGURACIÓN SEGURA (variables de entorno)
# ============================================================
# En Render debes crear estas variables en "Environment":
#   TELEGRAM_TOKEN   -> el token que te dio BotFather
#   TELEGRAM_CHAT_ID -> tu chat id (2023043563 en tu caso)
#   DB_PATH          -> opcional, por defecto /data/bot_loto.db
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DB_PATH = os.environ.get("DB_PATH", "/data/bot_loto.db")

if not TOKEN or not CHAT_ID:
    raise SystemExit("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en las variables de entorno.")

# ============================================================
# 2. LOGGING (para ver qué pasa en Render)
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot-loto")

# ============================================================
# 3. CONSTANTES
# ============================================================
ANIMALITOS = {
    "00": "Ballena", "1": "Carnero", "2": "Toro", "3": "Ciempiés", "4": "Alacrán",
    "5": "León", "6": "Rana", "7": "Perico", "8": "Ratón", "9": "Águila",
    "10": "Tigre", "11": "Gato", "12": "Caballo", "13": "Mono", "14": "Paloma",
    "15": "Zorro", "16": "Oso", "17": "Pavo", "18": "Burro", "19": "Chivo",
    "20": "Cochino", "21": "Gallo", "22": "Camello", "23": "Cebra", "24": "Iguana",
    "25": "Gallina", "26": "Vaca", "27": "Perro", "28": "Zamuro", "29": "Elefante",
    "30": "Caimán", "31": "Lapa", "32": "Ardilla", "33": "Pescado", "34": "Venado",
    "35": "Jirafa", "36": "Culebra",
}
CLAVES = list(ANIMALITOS.keys())

NOMBRES_METODOS = [
    "M1 (Frecuencia)", "M2 (Fríos)", "M3 (Simetría)", "M4 (Cruzado)",
    "M5 (Markov)", "M6 (Poisson)", "M7 (Regresión)", "M8 (Condicionada)",
    "M9 (Cluster)", "M10 (Genético)",
]

LOTERIA_T = "Lotto Activo Tradicional 🇻🇪"
LOTERIA_I = "Lotto Activo Internacional 🌍"

LEARNING_RATE = 0.05

# Pesos y estado en memoria (se hidratan desde SQLite al arrancar)
pesos = {LOTERIA_T: [0.10] * 10, LOTERIA_I: [0.10] * 10}
historial = {LOTERIA_T: [], LOTERIA_I: []}
ultimo_real = {LOTERIA_T: "Ninguno aún", LOTERIA_I: "Ninguno aún"}
aciertos_hoy = {LOTERIA_T: [0] * 10, LOTERIA_I: [0] * 10}
ganadores_hoy = {LOTERIA_T: [], LOTERIA_I: []}
resumen_enviado_hoy = False
ultimas_predicciones = {
    LOTERIA_T: {"horario": "", "sugerencias": []},
    LOTERIA_I: {"horario": "", "sugerencias": []},
}

# ============================================================
# 4. SERVIDOR WEB (para que Render no duerma el servicio)
# ============================================================
class ServidorFake(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot de Loto Activo operando correctamente.")

    def log_message(self, format, *args):
        pass  # Silenciar logs del servidor HTTP


def arrancar_servidor_web():
    puerto = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", puerto), ServidorFake)
    log.info(f"📡 Servidor web activo en el puerto {puerto}")
    server.serve_forever()

# ============================================================
# 5. BASE DE DATOS SQLITE (persistencia real)
# ============================================================
def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loteria TEXT NOT NULL,
            numero TEXT NOT NULL,
            fecha TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pesos (
            loteria TEXT NOT NULL,
            indice INTEGER NOT NULL,
            peso REAL NOT NULL,
            PRIMARY KEY (loteria, indice)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS aciertos (
            loteria TEXT NOT NULL,
            indice INTEGER NOT NULL,
            total INTEGER NOT NULL,
            PRIMARY KEY (loteria, indice)
        )
    """)
    conn.commit()
    return conn


def cargar_estado_desde_db():
    conn = init_db()
    c = conn.cursor()

    # Historial
    for lot in (LOTERIA_T, LOTERIA_I):
        c.execute("SELECT numero FROM historial WHERE loteria=? ORDER BY id ASC", (lot,))
        historial[lot] = [row[0] for row in c.fetchall() if row[0] in ANIMALITOS]
        log.info(f"📚 Historial {lot}: {len(historial[lot])} sorteos")

    # Pesos
    for lot in (LOTERIA_T, LOTERIA_I):
        c.execute("SELECT indice, peso FROM pesos WHERE loteria=?", (lot,))
        rows = c.fetchall()
        if rows:
            p = [0.10] * 10
            for idx, val in rows:
                if 0 <= idx < 10:
                    p[idx] = val
            pesos[lot] = p

    # Aciertos
    for lot in (LOTERIA_T, LOTERIA_I):
        c.execute("SELECT indice, total FROM aciertos WHERE loteria=?", (lot,))
        rows = c.fetchall()
        if rows:
            a = [0] * 10
            for idx, val in rows:
                if 0 <= idx < 10:
                    a[idx] = val
            aciertos_hoy[lot] = a

    conn.close()


def guardar_sorteo_db(loteria, numero):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO historial (loteria, numero, fecha) VALUES (?, ?, ?)",
        (loteria, numero, datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def guardar_pesos_db(loteria):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for i, p in enumerate(pesos[loteria]):
        c.execute(
            "INSERT OR REPLACE INTO pesos (loteria, indice, peso) VALUES (?, ?, ?)",
            (loteria, i, p),
        )
    conn.commit()
    conn.close()


def guardar_aciertos_db(loteria):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for i, a in enumerate(aciertos_hoy[loteria]):
        c.execute(
            "INSERT OR REPLACE INTO aciertos (loteria, indice, total) VALUES (?, ?, ?)",
            (loteria, i, a),
        )
    conn.commit()
    conn.close()

# ============================================================
# 6. TELEGRAM
# ============================================================
def enviar_mensaje_telegram(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    for intento in range(3):
        try:
            r = requests.post(url, json=payload, timeout=12)
            if r.status_code == 200:
                return True
            log.warning(f"Telegram intento {intento+1} falló: {r.status_code} {r.text[:120]}")
        except Exception as e:
            log.warning(f"Telegram excepción: {e}")
        time.sleep(3)
    return False

# ============================================================
# 7. MÉTODOS DE PREDICCIÓN (ahora SÍ son 10 reales)
# ============================================================
def _tripleta_aleatoria():
    return random.sample(CLAVES, 3)


def m1_frecuencia(hist):
    """Los 3 números más frecuentes del historial reciente."""
    if not hist:
        return _tripleta_aleatoria()
    recientes = hist[-100:]
    conteo = {}
    for n in recientes:
        conteo[n] = conteo.get(n, 0) + 1
    top = sorted(conteo, key=conteo.get, reverse=True)[:3]
    while len(top) < 3:
        top.append(random.choice(CLAVES))
    return top[:3]


def m2_frios(hist):
    """Los 3 números que hace más tiempo no salen."""
    if not hist:
        return _tripleta_aleatoria()
    ultima_aparicion = {n: -1 for n in CLAVES}
    for i, n in enumerate(hist):
        ultima_aparicion[n] = i
    frios = sorted(ultima_aparicion, key=lambda x: ultima_aparicion[x])[:3]
    return frios


def m3_simetria(hist):
    """Números que forman simetría respecto al último (último ± k)."""
    if not hist:
        return _tripleta_aleatoria()
    try:
        ultimo = int(hist[-1])
    except ValueError:
        return _tripleta_aleatoria()
    cand = []
    for k in (1, 2, 3):
        for v in (ultimo - k, ultimo + k):
            if 0 <= v <= 36 and f"{v:02d}" not in cand and str(v) not in cand:
                cand.append(f"{v:02d}" if v < 10 else str(v))
    while len(cand) < 3:
        cand.append(random.choice(CLAVES))
    return cand[:3]


def m4_cruzado(hist):
    """Mezcla el último real con los 2 más frecuentes."""
    if not hist:
        return _tripleta_aleatoria()
    ultimo = hist[-1]
    freq = {}
    for n in hist[-50:]:
        freq[n] = freq.get(n, 0) + 1
    top2 = sorted([n for n in freq if n != ultimo], key=freq.get, reverse=True)[:2]
    while len(top2) < 2:
        top2.append(random.choice(CLAVES))
    return [ultimo] + top2[:2]


def m5_markov(hist):
    """Markov: el que más veces siguió al último resultado."""
    if len(hist) < 2:
        return _tripleta_aleatoria()
    ultimo = hist[-1]
    siguientes = [hist[i + 1] for i in range(len(hist) - 1) if hist[i] == ultimo]
    if not siguientes:
        return _tripleta_aleatoria()
    mas_comun = max(set(siguientes), key=siguientes.count)
    resto = [n for n in CLAVES if n != mas_comun]
    return [mas_comun] + random.sample(resto, 2)


def m6_poisson(hist):
    """Aproximación Poisson: números con conteo cercano a la media."""
    if not hist:
        return _tripleta_aleatoria()
    conteo = {n: 0 for n in CLAVES}
    for n in hist[-200:]:
        if n in conteo:
            conteo[n] += 1
    media = sum(conteo.values()) / len(conteo) if conteo else 0
    cercanos = sorted(conteo, key=lambda x: abs(conteo[x] - media))[:3]
    return cercanos


def m7_regresion(hist):
    """Tendencia lineal simple sobre los últimos números."""
    if len(hist) < 3:
        return _tripleta_aleatoria()
    ultimos = []
    for n in hist[-10:]:
        try:
            ultimos.append(int(n))
        except ValueError:
            continue
    if len(ultimos) < 3:
        return _tripleta_aleatoria()
    pendiente = (ultimos[-1] - ultimos[0]) / max(1, len(ultimos) - 1)
    base = ultimos[-1]
    cand = []
    for k in (1, 2, 3):
        v = int(base + pendiente * k) % 37
        cand.append(f"{v:02d}" if v < 10 else str(v))
    while len(cand) < 3:
        cand.append(random.choice(CLAVES))
    return list(dict.fromkeys(cand))[:3]


def m8_condicionada(hist):
    """Condicionada: números que aparecen tras pares específicos."""
    if len(hist) < 4:
        return _tripleta_aleatoria()
    ultimo = hist[-1]
    anteriores = hist[-10:]
    cand = []
    for i, n in enumerate(anteriores[:-1]):
        if n == ultimo:
            cand.append(anteriores[i + 1])
    if not cand:
        return _tripleta_aleatoria()
    random.shuffle(cand)
    while len(cand) < 3:
        cand.append(random.choice(CLAVES))
    return list(dict.fromkeys(cand))[:3]


def m9_cluster(hist):
    """Cluster: agrupa los números en rangos y elige de un cluster."""
    if not hist:
        return _tripleta_aleatoria()
    clusters = {i: [] for i in range(4)}
    for n in hist[-150:]:
        try:
            clusters[int(n) // 10].append(n)
        except ValueError:
            continue
    cluster_top = max(clusters, key=lambda k: len(clusters[k]))
    pool = clusters[cluster_top] or CLAVES
    return random.sample(pool, min(3, len(pool))) if len(pool) >= 3 else _tripleta_aleatoria()


def m10_genetico(hist):
    """Genético simplificado: cruza el top frecuente con el último."""
    if not hist:
        return _tripleta_aleatoria()
    freq = {}
    for n in hist[-80:]:
        freq[n] = freq.get(n, 0) + 1
    top = sorted(freq, key=freq.get, reverse=True)[:5]
    ultimo = hist[-1]
    padres = list(dict.fromkeys([ultimo] + top))
    return random.sample(padres, min(3, len(padres))) if len(padres) >= 3 else _tripleta_aleatoria()


METODOS = [m1_frecuencia, m2_frios, m3_simetria, m4_cruzado, m5_markov,
           m6_poisson, m7_regresion, m8_condicionada, m9_cluster, m10_genetico]


def generar_prediccion(loteria):
    hist = historial[loteria]
    return [m(hist) for m in METODOS], pesos[loteria]

# ============================================================
# 8. SCRAPER REAL (con fallback honesto: si falla, devuelve None)
# ============================================================
def raspar_resultado_real(loteria, horario_buscado):
    """
    Intenta obtener el resultado real desde una web pública.
    ⚠️ IMPORTANTE: La URL y el parseo dependen de la web real.
    Debes adaptar los selectores a la página que uses.

    Si no se puede, devuelve None (NO un número aleatorio).
    """
    # TODO: reemplaza esta URL por la web real de resultados
    url = "https://loteriadehoy.com"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            log.warning(f"Scraper: HTTP {r.status_code}")
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.find_all(["div", "tr", "td"]):
            texto = item.get_text(" ", strip=True)
            if horario_buscado in texto:
                for num, nombre in ANIMALITOS.items():
                    if nombre.lower() in texto.lower():
                        return num
    except Exception as e:
        log.warning(f"Scraper excepción: {e}")
    return None  # 👈 honesto: no inventamos datos

# ============================================================
# 9. APRENDIZAJE
# ============================================================
def ajustar_pesos(loteria, numero_real):
    if numero_real is None or numero_real not in ANIMALITOS:
        log.info(f"⏭️  {loteria}: sin resultado real válido, no se aprende.")
        return

    nombre = ANIMALITOS[numero_real]
    ganadores_hoy[loteria].append(nombre)
    ultimo_real[loteria] = f"{numero_real} - {nombre}"

    pred = ultimas_predicciones[loteria]["sugerencias"]
    if not pred:
        return

    historial[loteria].append(numero_real)
    guardar_sorteo_db(loteria, numero_real)

    p = pesos[loteria]
    for idx, tripleta in enumerate(pred):
        if numero_real in tripleta:
            p[idx] += LEARNING_RATE
            aciertos_hoy[loteria][idx] += 1
        else:
            p[idx] -= LEARNING_RATE / 6
        if p[idx] < 0.01:
            p[idx] = 0.01

    s = sum(p)
    pesos[loteria] = [x / s for x in p]
    guardar_pesos_db(loteria)
    guardar_aciertos_db(loteria)

# ============================================================
# 10. RESUMEN DIARIO
# ============================================================
def enviar_resumen_diario():
    global resumen_enviado_hoy
    total = len(ganadores_hoy[LOTERIA_T]) + len(ganadores_hoy[LOTERIA_I])
    if total == 0:
        return
    lineas = "📊 *BALANCE DIARIO*\n───────────────\n"
    for lot in (LOTERIA_T, LOTERIA_I):
        if not ganadores_hoy[lot]:
            continue
        idx = aciertos_hoy[lot].index(max(aciertos_hoy[lot]))
        repetido = max(set(ganadores_hoy[lot]), key=ganadores_hoy[lot].count)
        lineas += f"\n*{lot}*\n"
        lineas += f"• Sorteos: `{len(ganadores_hoy[lot])}`\n"
        lineas += f"• Campeón: `{NOMBRES_METODOS[idx]}`\n"
        lineas += f"• Más repetido: `{repetido}`\n"
    if enviar_mensaje_telegram(lineas):
        for lot in (LOTERIA_T, LOTERIA_I):
            aciertos_hoy[lot] = [0] * 10
            ganadores_hoy[lot] = []
            guardar_aciertos_db(lot)
        resumen_enviado_hoy = True

# ============================================================
# 11. BUCLE PRINCIPAL
# ============================================================
def verificar_y_enviar():
    global resumen_enviado_hoy
    ahora = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
    hora, minuto = ahora.hour, ahora.minute

    if hora == 0 and minuto < 2:
        resumen_enviado_hoy = False

    if hora == 19 and 45 <= minuto <= 47 and not resumen_enviado_hoy:
        enviar_resumen_diario()
        time.sleep(61)
        return

    if hora < 7 or hora > 20:
        return

    # --- ALERTAS ---
    if minuto == 30:
        _enviar_alerta(LOTERIA_T, (ahora + datetime.timedelta(hours=1)).replace(minute=0).strftime("%I:%M %p"))
    elif minuto == 0:
        _enviar_alerta(LOTERIA_I, ahora.replace(minute=30).strftime("%I:%M %p"))

    # --- EVALUACIÓN (rangos amplios para no perder ventanas) ---
    if 42 <= minuto <= 45 and ultimas_predicciones[LOTERIA_T]["horario"]:
        h_e = ahora.replace(minute=0).strftime("%I:%M %p")
        ajustar_pesos(LOTERIA_T, raspar_resultado_real(LOTERIA_T, h_e))
        ultimas_predicciones[LOTERIA_T]["horario"] = ""

    elif 12 <= minuto <= 15 and ultimas_predicciones[LOTERIA_I]["horario"]:
        h_e = (ahora - datetime.timedelta(hours=1)).replace(minute=30).strftime("%I:%M %p")
        ajustar_pesos(LOTERIA_I, raspar_resultado_real(LOTERIA_I, h_e))
        ultimas_predicciones[LOTERIA_I]["horario"] = ""


def _enviar_alerta(loteria, sorteo_tiempo):
    tripletas, pesos_actuales = generar_prediccion(loteria)
    ultimas_predicciones[loteria] = {"horario": sorteo_tiempo, "sugerencias": tripletas}
    msg = (
        f"🚨 *ALERTA (10 MÉTODOS)* 🚨\n\n"
        f"📌 *Lotería:* {loteria}\n"
        f"⏰ *Sorteo:* {sorteo_tiempo}\n"
        f"🔙 *Anterior:* `{ultimo_real[loteria]}`\n"
        f"───────────────\n🔮 *Tripletas:*\n\n"
    )
    for i in range(10):
        nums = ", ".join(f"`{n}`" for n in tripletas[i])
        msg += f"• *{NOMBRES_METODOS[i]}* [{pesos_actuales[i]:.2f}] → {nums}\n"
    if enviar_mensaje_telegram(msg):
        time.sleep(60)


# ============================================================
# 12. ARRANQUE
# ============================================================
if __name__ == "__main__":
    log.info("🚀 Iniciando bot...")
    cargar_estado_desde_db()

    t = threading.Thread(target=arrancar_servidor_web, daemon=True)
    t.start()

    log.info("🤖 Bot activo. Patrullando cada 20s...")
    while True:
        try:
            verificar_y_enviar()
        except Exception as e:
            log.exception(f"Error en el bucle: {e}")
        time.sleep(20)
