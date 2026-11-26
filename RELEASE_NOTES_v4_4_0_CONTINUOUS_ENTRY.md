# PRIMA-15 v4.4.0 — Continuous Entry

## Qué corrige

La versión anterior podía quedarse sin abrir por dos razones combinadas:

1. El comité normal estaba configurado para revisar cada hora; las revisiones anticipadas dependían de un candidato `watch_candidate` poco frecuente.
2. El filtro TP-first podía convertir una dirección LONG/SHORT válida en WAIT cuando faltaban analogías, order book o presión microestructural suficiente. Además, el snapshot enviado a los LLM todavía contenía una regla contradictoria que decía que fast-entry era un requisito obligatorio.

## Comportamiento nuevo

- Comité OpenRouter cada 300 segundos (5 minutos) por defecto, esté la wallet FLAT o con posición abierta.
- Nueva consulta inmediata después de un cierre por Take Profit.
- Fast-entry es un diagnóstico y aporta advertencias; no veta LONG/SHORT de un comité válido cuando `FAST_ENTRY_ENFORCE_GATE=false` (predeterminado).
- La regla de incertidumbre SHORT se explica al modelo sin condicionarla al filtro fast-entry.
- Una posición se abre, mantiene o revierte solamente con dos respuestas LLM válidas, decisión final LONG/SHORT y precio fresco.
- Si OpenRouter falla, falta el quórum, los datos no están frescos o se alcanza el límite diario, no se inventa una señal ni se reutiliza una respuesta antigua.
- El panel muestra cadencia, comités usados, restantes y modo de fast-entry.

## Límites

- Máximo inicial: 20 comités por día UTC. Cada comité necesita normalmente dos llamadas principales; respaldos pueden añadir llamadas. Se puede ajustar `OPENROUTER_MAX_COMMITTEES_PER_DAY` dentro del máximo permitido por configuración, siempre respetando la cuota real de OpenRouter.
- `OPENROUTER_INTERVAL_SECONDS=300` es la cadencia de consulta normal. Después del límite diario, no habrá nuevas consultas hasta el siguiente día UTC.
- El TP se comprueba en cada sondeo del precio, de forma independiente al comité.
- La frecuencia de 5 minutos no garantiza una operación cada 5 minutos: se requieren datos frescos, clave válida y dos respuestas utilizables. Las posiciones siguen siendo exclusivamente simuladas.

## Migración

Copiar `.env` y `data/prime_wave.sqlite3` desde la versión anterior. La configuración convierte automáticamente los valores antiguos exactos de 3600 o 7200 segundos a 300. Si el `.env` contiene otro intervalo personalizado, conservará ese valor; para activar el ciclo de 5 minutos, revisar que figure `OPENROUTER_INTERVAL_SECONDS=300`.

## Verificación

- 83 pruebas unitarias superadas.
- `python -m py_compile *.py` correcto.
- `node --check static/app.js` correcto.
- La integración real con OpenRouter y los exchanges debe comprobarse desde el despliegue del usuario; no se ha simulado una respuesta real de proveedor.
