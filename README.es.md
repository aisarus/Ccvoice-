# Voice Shell for Claude Code

[English](README.md) · [Русский](README.ru.md) · **Español** · [中文](README.zh.md)

Habla con una sesión de Claude Code que ya está corriendo sin sacar el teléfono
del bolsillo: la pantalla apagada, la respuesta en el oído.

```
tú
  ↕ voz
aplicación Android
  ↕ WebSocket (LAN / Tailscale / HTTPS)
demonio
  ↕
una sesión de Claude Code de larga vida
  ↕
repositorio / shell / git / tests
```

No es otro agente. Es transporte de voz, enrutado y una capa de presentación
sobre una sesión que ya funciona.

---

## Por qué esto y no lo que ya existe

Claude Code ya tiene modo de voz: mantienes pulsada la barra espaciadora y
escribe lo que dices. Eso es dictado — sigues delante de la máquina, sigues
leyendo la pantalla.

Claude Code Remote Control y la aplicación móvil de Cursor ponen la sesión en la
pantalla del teléfono. Eso es un mando a distancia — sigues mirando algo.

Aquí no hay nada en pantalla. La palabra de activación se reconoce en el propio
teléfono, así que el micrófono puede quedarse abierto sin que el audio salga del
dispositivo. Dices «Клод», esperas el tono y hablas. Claude trabaja en el
repositorio y te cuenta el resultado en una a tres frases. Puedes cortarlo a
mitad de respuesta. Puedes darle un encargo e irte: las tareas en segundo plano
corren en su propio worktree de git y avisan cuando el oído está libre.

El precio es un alcance estrecho. Lee [Límites](#límites) antes de instalar.

---

## Qué hace falta

| | |
|---|---|
| Una máquina que no se apague | Un VPS o tu propio ordenador en casa. El demonio se ha ejecutado en Linux; no se ha probado en nada más. |
| Acceso a Claude | Una suscripción a Claude (Pro o Max) si lo ejecutas para ti mismo, o una clave de API de Anthropic. Ver [Con qué autenticarse](#con-qué-autenticarse). |
| Un teléfono Android | La aplicación es solo para Android. No hay aplicación de iOS; iOS está en la última etapa de la hoja de ruta. |
| Auriculares Bluetooth | No son obligatorios, pero son el motivo de todo esto. La aplicación activa el micrófono de los propios auriculares para que el teléfono no escuche a través de la tela. |

También hay un cliente de navegador, para probarlo sin instalar nada. Es más
limitado: no hay palabra de activación ni «stop» siempre disponible. Ver
[`docs/DEPLOY.md`](docs/DEPLOY.md).

### Con qué autenticarse

Una suscripción Claude Pro o Max sirve cuando **tú** ejecutas esto en **tu
propia** máquina **para ti**. Desde febrero de 2026 Anthropic no permite usar
las credenciales OAuth de una suscripción desde productos de terceros: no puedes
levantar esto como servicio y dejar que otras personas hablen con él a costa de
tu suscripción. Si alguien que no seas tú va a usar una instancia, esa instancia
necesita su propia clave de API de Anthropic.

La pregunta que originó todo esto, y la carta escrita al respecto, están en
[`docs/anthropic-inquiry.md`](docs/anthropic-inquiry.md).

---

## Instalación

### El servidor

Por ssh, desde el teléfono si es lo que tienes a mano:

```bash
# primero mirar, sin tocar nada
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | CHECK=1 bash

# después instalar
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo bash
```

El script instala Node, la CLI de Claude Code y las dependencias de Python,
ejecuta los tests, escribe una unidad de systemd e imprime una dirección y un
token. Se detiene en los tests: si fallan, el servicio no arranca.

Apunta un dominio al servidor y añade HTTPS con Caddy:

```bash
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo DOMAIN=voice.example.com bash
```

Sin dominio el puerto queda abierto y el tráfico va en HTTP plano. El token
mantiene fuera a los extraños; no cifra la conversación. Usa un dominio, o
Tailscale con el puerto cerrado por cortafuegos.

Después, el servidor se lleva con dos comandos:

```bash
sudo bash /opt/voice-shell/scripts/update-server.sh   # fetch + reset, reinicio
sudo bash /opt/voice-shell/scripts/doctor.sh --fast   # un informe de todo
```

Actualizar no es `git pull`. Con ramas divergentes, `pull` se detiene y pregunta
cómo fusionar; aquí no hay nada que fusionar, la rama es la verdad.
`update-server.sh` hace fetch + reset, imprime lo que va a desaparecer y deja
una etiqueta `before-update-…` a la que volver.

### El demonio en tu propio ordenador

Una máquina en la nube no ve la tuya. Para que Claude Code trabaje sobre
proyectos reales, el demonio corre donde están los proyectos:

```bash
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-desktop.sh | bash -s -- ~/tu-proyecto
```

Clona el repositorio, instala dependencias, ejecuta los tests, genera un token y
arranca el demonio. Desde fuera de casa, por Tailscale.

Si hay una sesión de Claude Code sentada en ese ordenador, dale
[`docs/desktop-handoff.json`](docs/desktop-handoff.json): los mismos pasos en
formato legible por máquina, incluido qué comprobar y qué contarte.

### La aplicación

[Descargar el APK](https://github.com/aisarus/Ccvoice-/releases/download/apk-latest/app-debug.apk)
— se recompila en cada push a la rama de trabajo. Concede permisos de micrófono
y notificaciones: sin notificaciones Android no deja funcionar un servicio en
primer plano. Después, tres campos una sola vez: dirección, token e idioma
inicial.

> **El APK publicado está firmado con una clave de depuración que vive en este
> repositorio, contraseña incluida** — el keystore es
> `android/app/voice-shell.keystore` y la contraseña está en texto plano en
> `android/app/build.gradle.kts`. Está ahí a propósito: si no, cada
> compilación de CI recibiría una clave aleatoria nueva y las actualizaciones no
> se instalarían encima. La consecuencia es real: cualquiera puede compilar un
> APK firmado con esa misma clave y Android lo aceptará como actualización del
> tuyo. **Instala el APK solo desde las releases de este repositorio.** Si
> compilas el tuyo, genera tu propio keystore y no lo metas en el repositorio.

---

## Cómo hablar

En dos tiempos. Esta es la parte que la gente hace mal:

> «Клод» · pausa · espera el tono · «ejecuta los tests»

La palabra de activación la oye un modelo pequeño en el teléfono (Vosk, ~45 MB,
se descarga en el primer arranque). Su único trabajo es «Клод» y «stop». La
frase en sí va al reconocedor del propio teléfono, que es mucho mejor pero no
arranca al instante. Dicho todo de un tirón también funciona, peor: a los 3,5
segundos la aplicación deja de esperar y envía lo que oyó el modelo pequeño.

El tono después de «Клод» es el permiso para hablar. Si hablas antes, se pierde
media frase.

| Sonido | Qué significa |
|---|---|
| pitido corto + vibración | micrófono abierto, habla |
| clic suave + vibración corta | frase aceptada y enviada |
| pitido grave | no oyó nada, el micrófono se cerró en vacío |

La palabra de activación tolera una letra de diferencia: «клот», «клад»,
«плод» también cuentan. «Код», «чат», «кот», «что», «как» no cuentan nunca: así
empiezan los comandos. Decirla durante una respuesta es una interrupción: Claude
se calla y escucha.

Cada frase va a uno de tres destinos, y el demonio elige si no nombras uno:

| Destino | Qué es | Cambia el proyecto |
|---|---|---|
| `код` | Claude Code en el directorio de trabajo | sí |
| `чат` | una conversación aparte sin acceso al proyecto; solo búsqueda web y lectura de páginas | no |
| `заметка` | una línea añadida a un archivo de notas | no |

Lo ambiguo va a `чат` a propósito: un `чат` equivocado cuesta una frase, un
`код` equivocado ya ha hecho algo.

El shell también resuelve por su cuenta el deshacer, la memoria, las tareas en
segundo plano, el puente a Telegram y las confirmaciones por voz, sin despertar
a Claude. Todas las frases que conoce están en [`docs/VOICE.md`](docs/VOICE.md).

### Cuatro idiomas, y no hace falta elegir uno

El shell habla y escucha en **inglés, ruso, español y chino**. Los prefijos de
enrutado, deshacer, memoria, tareas en segundo plano, el puente a Telegram y el
sí/no ante una petición de permiso existen en los cuatro, igual que todo lo que
el shell contesta. La interfaz del teléfono está traducida a los mismos cuatro.

Hablar y escuchar se deciden de forma distinta, a propósito:

- **Lo que oye** no se configura. Las frases de comando de todos los idiomas se
  buscan a la vez, porque el shell no puede saber en qué idioma va a ser tu
  siguiente frase, y pedirte que muevas un interruptor antes de hablar anularía
  el sentido de una interfaz manos libres. Es seguro porque son órdenes, no
  prosa: las tablas son cortas y están elegidas para no chocar entre sí.
- **Lo que dice** se decide en cada frase: lo que declaró el teléfono al
  conectarse, luego el idioma en el que acabas de hablar, luego `VOICE_LANG`, y
  por último inglés. Cambia al inglés a mitad de conversación y la respuesta
  vuelve en inglés, sin tocar ningún ajuste.

Las tablas están en [`daemon/voice_claude/lexicon.py`](daemon/voice_claude/lexicon.py)
(lo que oye) y [`daemon/voice_claude/i18n.py`](daemon/voice_claude/i18n.py) (lo
que dice). Son diccionarios normales: añadir un quinto idioma es añadir una
columna, y `tests/test_i18n.py` te dirá qué te has dejado.

Límites honestos: la redacción rusa es la que está en uso diario y el inglés es
la traducción de referencia. El español y el chino se tradujeron con cuidado
pero no los ha revisado un hablante nativo, y las frases de comando en
particular agradecerían que alguien que las use diga cuáles suenan mal. El
hebreo solo está como idioma de reconocimiento: el shell no tiene tablas de
frases para él, así que hablado en hebreo esto es una tubería de voz hacia
Claude y poco más.

---

## Piezas opcionales

**GitHub.** El destino `код` corre sobre un shell real, así que GitHub funciona
con `gh` en cuanto tiene un token:

```bash
sudo bash /opt/voice-shell/scripts/setup-github.sh ghp_TU_TOKEN "Tu Nombre" tu@example.com
```

Necesita root y el servicio ya levantado: escribe en `/etc/voice-shell.env`.
Token: [github.com/settings/tokens](https://github.com/settings/tokens) →
classic → permisos `repo` y `workflow`. Un token inválido se rechaza en el acto
en vez de guardarse como sorpresa para más tarde.

**Telegram.** Un único chat fijado de antemano, para que un archivo pueda salir
de la máquina por voz sin que la voz de un extraño pueda redirigirlo:

```bash
sudo bash /opt/voice-shell/scripts/setup-telegram.sh
```

Te pide el token del bot a @BotFather, averigua el id del chat a partir del
primer mensaje que le envíes al bot y manda ahí un mensaje de prueba.

**Confirmaciones por voz.** Desactivadas por defecto: `PERMISSION_MODE` es
`auto` y no se pregunta nada. `guarded` pregunta antes de lo destructivo, `ask`
pregunta antes de todo salvo lecturas seguras.

---

## Ejecutarlo en local

```bash
pip3 install -r daemon/requirements-dev.txt
cd daemon && python3 -m voice_claude --workspace ~/tu-proyecto
```

El cliente y el WebSocket comparten un puerto (`8787` o `$PORT`); el demonio
imprime un token al arrancar. Sobre `http://` el navegador solo da micrófono a
`localhost`.

Sin credencial conectada, `code` y `chat` responden con un sustituto. El bucle
recorre igualmente todo el camino y oyes un honesto «Claude no disponible: …»
con el motivo, en lugar de una respuesta inventada.

```bash
python3 -m pytest tests -q        # 250 passed
python3 scripts/validate_spec.py  # OK: voice-shell-for-claude-code v0.2.0 (29 top-level sections)
```

El validador funciona sin dependencias y comprueba solo las referencias
cruzadas; `pip install jsonschema` añade la validación por esquema.

---

## Límites

**Lo que no está hecho:**

- **Detección de fin de frase (VAD) en el dispositivo.** La aplicación decide
  que has terminado por temporizador, no porque te haya oído callar.
- **Huella de voz.** El clasificador de hablante tiene un perfil
  `with_voiceprint` con sus pesos, pero la aplicación nunca mide el parecido de
  voz, así que ese perfil no llega a usarse. El rol se decide solo por acústica.
- **Multiproyecto.** Un directorio de trabajo por demonio, sin cambiar de sesión
  por voz.
- **Reconocimiento de voz en el servidor.** Reconoce el teléfono o, en el
  cliente de navegador, Google. El demonio no ve audio en ningún momento.
- **Sugerencias proactivas en modo ambient.** `passive` funciona: el búfer
  guarda diez minutos y responde preguntas sobre él. Los disparadores de
  `assist` están definidos en la especificación y no se activan nunca.
- **iOS.** No.

**Hecho pero sin ajustar:**

- **La identificación del hablante** está implementada y cubierta por tests
  sobre perfiles sintéticos. Nunca se ha calibrado con grabaciones reales, así
  que los umbrales son conjeturas. En frío, es de esperar que tu propia voz baja
  vuelva como `unknown`.

**Aristas:**

- «Откати» por sí solo es un comando de deshacer. Los comandos del shell se
  buscan al principio de la frase, tras muletillas como «клод», «давай», «ну»,
  de modo que hablar sobre deshacer es seguro — pero una frase que empiece por
  «откати» hace un `git reset --hard` real hasta el punto anterior. El commit
  revertido permanece en el historial de git.
- Las tareas en segundo plano requieren que el directorio de trabajo sea un
  repositorio git. Como mucho corren dos a la vez.
- Telegram se niega a enviar `.env`, claves, keystores o nombres que contengan
  token/secret/password, cualquier archivo fuera del directorio de trabajo y
  cualquiera de más de 45 MB. Se niega en voz alta y dice por qué.
- El demonio bajo systemd suele correr como root. La CLI de Claude se niega a
  arrancar como root con los permisos desactivados, así que ese comportamiento
  se aporta mediante un callback.
- El punto de restauración se escribe por frase, no por archivo: deshacer
  retira todo lo que cambió una misma frase.

---

## Qué hay en este repositorio

| Ruta | Qué es |
|---|---|
| [`spec/voice-shell.json`](spec/voice-shell.json) | La especificación completa, legible por máquina — la única fuente de verdad |
| [`spec/voice-shell.schema.json`](spec/voice-shell.schema.json) | JSON Schema (draft 2020-12) de la especificación |
| [`scripts/validate_spec.py`](scripts/validate_spec.py) | Validador: esquema más comprobaciones cruzadas de coherencia |
| [`daemon/`](daemon/) | `voice-claude-daemon`: roles de hablante, enrutado, formato de voz, WebSocket |
| [`android/`](android/) | La aplicación: palabra de activación local, segundo plano, botón del auricular |
| [`client/web/`](client/web/index.html) | Cliente para Chrome en Android: pulsar para hablar, STT, TTS, earcons |
| [`tests/`](tests/) | 250 tests, incluidos end-to-end sobre el protocolo real |
| [`Dockerfile`](Dockerfile) · [`render.yaml`](render.yaml) | Despliegue de un clic, desde el teléfono |
| [`docs/VOICE.md`](docs/VOICE.md) | Todas las frases que el sistema entiende por sí mismo |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Síntoma → qué comprobar → cómo arreglarlo |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Despliegue paso a paso sin ordenador |
| [`docs/desktop-handoff.json`](docs/desktop-handoff.json) | Encargo legible por máquina para una sesión de Claude Code en tu PC |

---

## Decisiones de diseño

- **Una sola acción física: `ACTIVATE`.** Nada de toques, dobles toques o
  deslizamientos — eso convierte el sistema en un mando de televisión. Todo lo
  demás es habla y contexto.
- **El botón del auricular es el canal de reserva.** Reparto buscado: ~90%
  palabra de activación, ~9% la ventana de diálogo siguiendo sola, ~1% botón.
- **Ventana de diálogo de 15 s.** Tras una respuesta no hace falta activar.
- **Tu habla no se reescribe nunca.** La salida de Claude se resume en una a
  tres frases; la salida completa se queda en pantalla.
- **La interrupción está partida en dos.** «Стоп» corta solo la voz. «Останови
  работу» interrumpe Claude Code.
- **Enrutar por sentido, no por palabras clave.** Un diccionario resuelve lo
  evidente al instante; en habla real un modelo rápido elige el destino en 2,5
  segundos o no lo elige. Un prefijo dicho, un chip de la interfaz o una
  corrección previa ya son la decisión de la persona y no se cuestionan.
- **Una única sesión de larga vida**, no un Claude nuevo por petición.
- **Ninguna nube propia.** LAN en casa, Tailscale fuera.

### Quién está hablando

La aplicación decide un rol para cada segmento de habla y se lo pasa a Claude
como una línea de servicio delante de la transcripción:

```
[voice-shell] speaker=master (говорит мастер) confidence=0.93 device=phone_mic

Arregla el fallo y ejecuta los tests.
```

El volumen es la señal principal pero no la única: una voz ajena cercana, o tu
propio comentario en voz baja, romperían un clasificador basado solo en nivel.
La decisión es una regresión logística ponderada sobre volumen relativo, SNR,
relación directo/reverberado, C50, contenido de altas frecuencias, efecto de
proximidad, una huella de voz local opcional y priores de dispositivo y
continuidad del diálogo.

| Rol | Qué significa | Qué puede hacer |
|---|---|---|
| `master` | habla cercana y limpia del dueño | todo: activar, mandar, interrumpir, confirmar |
| `bystander` | otra persona, la televisión, la habitación de al lado | nada; por defecto ni siquiera se envía |
| `unknown` | no hay confianza suficiente | no se ejecuta nada, una repregunta corta |
| `self_echo` | nuestro propio TTS en el micrófono | se descarta |

Cuando falta confianza el rol pasa a `unknown` en lugar de inventarse. Las
confirmaciones se aceptan solo de `master` con confianza ≥ 0,85.

### El segundo oído (ambient)

Desactivado por defecto. Tres submodos: `off`, `passive` (una transcripción
circular local de diez minutos que no sale del dispositivo hasta que preguntas
algo) y `assist` (la transcripción se envía al destino de chat).

El audio en bruto no se guarda nunca, el búfer se borra al salir del modo y el
habla de otras personas no entra en él sin un permiso aparte. Grabar a otras
personas está regulado de forma distinta según la jurisdicción: por eso esto es
un ajuste con un valor por defecto privado y no un detalle de implementación.

Las respuestas en ambient son `whisper_output`: una frase, doce palabras como
máximo, −6 dB, solo en un hueco de silencio de 1,2 s o más y nunca por encima de
tu voz. La palabra de activación está apagada en ambient — decir «Клод» delante
de gente es exactamente lo que este modo evita.

---

## Traducciones

[English](README.md) · [Русский](README.ru.md) · **Español** · [中文](README.zh.md)
