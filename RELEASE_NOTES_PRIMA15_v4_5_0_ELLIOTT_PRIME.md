# PRIMA-15 v4.5.0 — Elliott + Prime Hybrid

## Cambios

- Nuevo módulo `elliott_wave.py` con ZigZag adaptativo por ATR, pivotes confirmados y extremo provisional.
- Conteo por grados 1m, 15m, 1h y 4h.
- Descarga histórica paginada de hasta 5.000 velas de 15m y agregación alineada 1h/4h.
- Evaluación de impulsos estándar 1–2–3–4–5 y reglas duras de Elliott.
- Conteos parciales, ABC morfológico, posible triángulo contractivo y posible diagonal, siempre marcados como provisionales cuando no se verifican las subdivisiones de grado inferior.
- Ratios Fibonacci y alternancia expuestos como guías, nunca como reglas que rescatan un impulso inválido.
- Overlay de números primos en la duración de cada tramo Elliott y síntesis de confluencia/conflicto.
- Panel visual con cuatro grados temporales.
- Dos nuevas familias obligatorias en el comité OpenRouter: `elliott` y `elliott_prime` (20 familias en total; 21 preguntas tipadas para Perplexity Decider contando la decisión).
- Context schema sube a `prima15.llm-context.v5-elliott-prime`.
- `/api/history` admite solicitar hasta `HISTORY_CANDLES` (5.000 por defecto); sigue devolviendo 300 por defecto.
- Documentación teórica y metodológica en `ELLIOTT_WAVE_HYBRID_PRIMA15_v4_5.md`.

## Historial

`HISTORY_CANDLES=5000` significa aproximadamente 52,1 días de velas 15m. Desde ellas se obtienen 1.250 barras 1h y 312 barras 4h completas. El grado 1m reutiliza hasta 3.000 barras del módulo de entrada rápida.

Las descargas 15m se solicitan en páginas de hasta 1.000 velas y se almacenan en SQLite. Si la base tiene menos velas de las solicitadas, la aplicación intenta completar todo el objetivo; si el proveedor falla o no devuelve todo el historial, se declara el conteo real disponible.

## Compatibilidad / migración

- Se conserva la estructura SQLite existente; la evolución agrega cálculo/visualización y no requiere borrar la base.
- Copiar `.env` y `data/prime_wave.sqlite3` de la instalación anterior.
- Verificar que `HISTORY_CANDLES=5000` en `.env` local y en las variables de Render.
- El endpoint gráfico mantiene 300 velas por defecto, pero permite `GET /api/history?limit=5000`.

## Validación local

- 98 pruebas unitarias superadas.
- `python -m py_compile *.py` superado.
- `node --check static/app.js` superado.
- Prueba sintética con 5.000 velas 15m y 3.000 velas 1m: cálculo multigrado aproximado 0,08 s en el entorno de desarrollo.
- No se hizo conexión de mercado real ni llamada real a OpenRouter en el entorno de empaquetado.

## Límites

El conteo es un algoritmo heurístico y no un conteo manual certificado. El número de ondas depende del ZigZag ATR y del grado. Las subestructuras internas de ABC, diagonales y triángulos no se afirman como confirmadas si solo se observan pivotes exteriores. Cualquier backtest debe usar `confirmed_at`, no retrofechar la señal al timestamp original del extremo. La confluencia de duración prima no demuestra causalidad ni una ventaja rentable.
