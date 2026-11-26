# Modelos OpenRouter usados por PRIMA-15

## Comité principal (2 votos)

1. `perplexity/pplx-decider-flash-v1.3:free` — clasificador de decisiones con salida estructurada; usa `POST /api/alpha/decisions`.
2. `nvidia/nemotron-3.5-lightning:free` — modelo de texto/razonamiento; se limita el presupuesto de razonamiento para reservar espacio a la respuesta final.

## Respaldos, en orden

1. `baidu/ernie-5.5-flash-preview:free` — modelo conversacional de evaluación.
2. `perplexity/pplx-decider-v1.3:free` — Decisions API.
3. `nvidia/nemotron-3-ultra-550b-a55b:free` — texto y razonamiento.
4. `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` — modelo multimodal con salida de texto.
5. `recursive/flint-v1.0:free` — usa Anthropic Messages (`POST /api/v1/messages`).
6. `nvidia/nemotron-3.5-super-vl:free` — visión/lenguaje, salida textual.
7. `particle-ai/particle-1:free` — respaldo general, aunque su ficha está orientada a código/ingeniería.
8. `liquid/lfm-3-1.7b:free` — modelo de texto compacto.

Un respaldo sustituye un puesto fallido; nunca suma un voto extra. El comité necesita dos respuestas independientes válidas.

## Modelos de tu lista que no votan directamente

- `recraft/recraft-v4.1-flash-v2:free`: genera imágenes, no respuestas de mercado.
- `nvidia/nemotron-3.5-embed-1b:free` y `nvidia/nemotron-3.5-embed-8b:free`: producen vectores de embedding.
- `nvidia/nemotron-3.5-rerank-8b:free`: reordena resultados.
- `respan/span-01-lite:free`: no se usa como modelo conversacional direccional en este comité.
- `fish-audio/s2.1-pro-free:free`: sintetiza voz/audio.

## Información que recibe el comité (snapshot común v5 · Elliott + primos)

Cada modelo recibe el mismo snapshot JSON (la instrucción de rol cambia, no los datos). El envío incluye la política de decisión vigente (quórum, roles, umbrales, conducta ante excepción/fallo y parámetros de simulación), el output completo de `engine.analyze`, todas las escalas temporales primas, todas las piernas de amplitud, todos los casos analógicos, todas las lecturas de mechas, los últimos 350 candles OHLCV del motor, contexto 24h, cartera porcentual y 20 trades recientes, estadísticas de aprendizaje, backtest y hasta 20 ejemplos, 20 señales recientes, hasta 12 predicciones previas con outcomes ya cerrados y todos los campos del perfil adaptativo. El manifiesto declara explícitamente lo que se envió y los límites de las series históricas.

Cada voto tiene 20 clasificaciones obligatorias: market, cross_market, raw_candles, engine, policy, temporal, prime, amplitude, behavior, wick, trend, signals, backtest, prior_memory, portfolio, learning, adaptive_profile y fast_entry. Si falta una o la opción no es válida, se descarta esa respuesta y se busca respaldo. La interfaz muestra la cobertura, tamaño de snapshot y SHA-256 común.

Perplexity Decider responde mediante 21 preguntas tipadas (dirección + 20 familias de evidencia) sobre el mismo estado; no entrega basis narrativo, sino distribuciones por opción. Los modelos conversacionales deben incluir `basis` textual en cada una de las 20 secciones.

Cada evaluación incluye las tres dimensiones originales de onda y una cuarta capa estructural Elliott:

- **Temporal:** escalas primas activas, P del cierre en cada ventana, coincidencia de duraciones, primo cercano y diferencia respecto al primo.
- **Amplitud:** amplitud porcentual por tramo, duración, velocidad, ATR normalizado, contracción/expansión, proporción entre ondas del mismo sentido y porcentaje de retroceso.
- **Comportamiento:** huella de 13 velas, fase detectada y analogías históricas resueltas a 4, 8 y 12 velas.
- **Mechas:** P15, proporción mecha/rango, mecha/cuerpo, P de extremos en ventanas primas, recuperación de soporte o rechazo de resistencia, volumen, RSI, impulso, divergencia, razones y bloqueadores.
- **Elliott multigrado:** conteos 1m/15m/1h/4h, pivotes confirmados, patrón seleccionado, reglas duras, estado provisional, ratios Fibonacci orientativos, grado de invalidación y próxima hipótesis. La relación Elliott-primos añade duración de cada tramo, primo cercano, diferencia y confluencia/conflicto direccional.

El primer LLM recibe el rol de excepción/rechazo. Nemotron 3.5 Lightning recibe el rol de continuidad. Si una llamada falla, el respaldo sustituye el puesto ausente conservando el mismo rol analítico.


## Conteo Elliott multigrado (v4.5.0)

PRIMA-15 procesa hasta 5.000 velas cerradas de 15m (aprox. 52,1 días) y agrega desde esas velas 1.250 de 1h y 312 completas de 4h. El grado 1m utiliza hasta 3.000 velas del motor rápido. Elliott no reemplaza el motor de primos: forma una lectura estructural paralela que se cruza con la duración prima de cada tramo.

El contador usa ZigZag con umbral ATR-adaptativo. Un máximo/mínimo solo se confirma tras una reversión posterior suficiente; el extremo actual se mantiene provisional. Busca impulsos estándar 1-2-3-4-5, impulsos parciales, correcciones ABC, triángulos contractivos y diagonales posibles. Comprueba las reglas duras de un impulso estándar: la onda 2 no atraviesa el origen de la 1, la onda 3 supera el extremo de la 1 y no es la más corta, y la onda 4 no solapa el territorio de la 1. Fibonacci y alternancia son guías; la forma sharp/sideways de las ondas 2/4 no se afirma sin subondas inferiores.

Por cada tramo se envía dirección, retorno porcentual, amplitud, duración en velas y minutos, primo más cercano y diferencia. `prime_elliott_synthesis` marca confluencia, conflicto direccional, ajuste temporal aislado o falta de un conteo válido. Un patrón ABC o impulso parcial puede mostrar confluencia provisional, pero nunca se rotula como impulso completo.

Los LLM reciben `elliott_wave` con los grados 1m/15m/1h/4h, hasta 24 pivotes confirmados por grado, el conteo seleccionado, reglas, ratios y advertencias; reciben además `prime_elliott_synthesis`. Cada uno debe clasificar las dos familias `elliott` y `elliott_prime`. Si el conteo es ambiguo, deben declarar `NO_CLEAR_COUNT` o `INSUFFICIENT`, no forzar una lectura.

Las 5.000 velas de 15m se procesan localmente, pero no se envían como 5.000 filas crudas a los LLM. El snapshot incluye sus resultados Elliott por grado y las últimas 350 velas crudas del motor técnico. Esto conserva el contexto histórico sin multiplicar innecesariamente el tamaño del prompt.

## Filtro de entrada rápida TP-first (v4.4.0)

Además de las dimensiones originales, el comité recibe un resumen cuantitativo de entrada rápida basado en hasta 2.400 velas 1m históricas y hasta 120 velas 1m recientes. El resumen incorpora retornos de 1/3/5/10m, EMA 9/21, VWAP, RSI 7, aceleración, eficiencia de recorrido, pulso de volumen, order book, flujo taker reciente (solo operaciones de los últimos 45s), spread, analogías TP-first, niveles 1m/15m y contexto BTC/ETH a 3/5/10m, incluida la fuerza relativa de RENDER frente a los líderes.

Las analogías seleccionadas deben separarse temporalmente por al menos 20 minutos. El modelo debe revisar el desacople relativo de RENDER (para no vetar mecánicamente una moneda que resiste mejor que BTC/ETH), la tasa histórica TP-first y su límite Wilson inferior al 80%, el tiempo mediano al objetivo, la presión viva, el obstáculo 1m/15m, el spread y la diferencia de calidad LONG/SHORT. Si ambos lados quedan a menos de 4,5 puntos, se bloquean ambos: no se fuerza una dirección. Por defecto, fast-entry es evidencia orientativa y no bloquea un consenso LLM válido; el veto solo se activa con `FAST_ENTRY_ENFORCE_GATE=true`.

El filtro fue diseñado para encontrar entradas que históricamente tocaron primero el TP de 0,20% frente a una barrera adversa de 0,12% dentro de 20 minutos. Esa barrera es únicamente una etiqueta de evaluación histórica, no una orden stop-loss activa. Las tasas TP-first describen analogías pasadas y no equivalen a la probabilidad calibrada de que la próxima operación gane.

## Jerarquía LONG / SHORT

1. **Sin anomalía confirmada:** Nemotron 3.5 Lightning ocupa el puesto de continuidad y estudia las ventanas de 3, 5 y 8 velas. La dirección base solo está establecida si al menos dos de las tres ventanas coinciden (>60%). Nemotron debe coincidir con esa dirección y alcanzar `AI_MIN_CONFIDENCE=30`. UP + coincidencia → LONG; DOWN + coincidencia → SHORT.
2. **Con anomalía confirmada:** una mecha inferior exige vela cerrada, geometría suficiente, posición extrema en escalas, recuperación real de soporte y al menos otra confirmación independiente. Para una mecha superior se exige la condición simétrica de rechazo de resistencia. En cualquiera de estos casos prevalece el primer LLM (Perplexity), con convicción mínima configurada.
3. **Incertidumbre válida:** sin tendencia >60%, si Nemotron contradice la continuidad o si el primer LLM no confirma una anomalía, se resuelve SHORT por la política deliberadamente asimétrica de la simulación.
4. **Fallo técnico:** con menos de dos respuestas LLM válidas el resultado es WAIT. No abre ni revierte una posición; conserva la existente. Un 401/429, respuesta vacía o timeout nunca se interpreta como señal de mercado.

La convicción de los modelos no es una probabilidad calibrada de rentabilidad. El sesgo SHORT ante incertidumbre puede perjudicar la simulación en tendencias alcistas; el comportamiento debe evaluarse con historial walk-forward y costes.

## Protección del comité

- Dos puestos independientes deben quedar cubiertos antes de aceptar una evaluación.
- Un modelo de respaldo reemplaza un puesto fallido y no se cuenta como voto adicional.
- Dos respuestas HTTP 429 consecutivas cortan el failover para no insistir durante un posible límite global.
- Las claves API solo se leen desde `.env` o variables de entorno.
- La cobertura 100% confirma que las 20 clasificaciones estructuradas llegaron; no prueba que el razonamiento interno sea correcto. La evidencia se contrasta con el precio y outcomes posteriores.
