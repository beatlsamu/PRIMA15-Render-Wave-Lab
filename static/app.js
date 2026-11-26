(() => {
  const $ = (id) => document.getElementById(id);
  const fmtPrice = (value) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat('es-CL', { minimumFractionDigits: 3, maximumFractionDigits: 5 }).format(Number(value));
  };
  const fmtPct = (value, digits = 2) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const n = Number(value);
    return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
  };
  const fmtTime = (ms) => {
    if (!ms) return '—';
    return new Date(Number(ms)).toLocaleString('es-CL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  };
  const fmtClock = (ms) => ms ? new Date(Number(ms)).toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
  const alertedEvents = new Set();
  let notifyAllowed = false;
  let toastTimer = null;
  let latestState = null;

  function showToast(message) {
    const toast = $('toast');
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 5000);
  }

  function setConnection(status, error) {
    const dot = $('connectionDot');
    dot.className = `dot ${status || 'offline'}`;
    $('connectionText').textContent = status === 'connected' ? 'MERCADO CONECTADO' : status === 'degraded' ? 'DATOS PARCIALES' : status === 'starting' ? 'CONECTANDO' : 'SIN CONEXIÓN';
    if (error && status !== 'connected') $('connectionText').title = error;
  }

  function updateSignals(s) {
    const a = s.analysis || {};
    const long = Number(a.long_score || 0);
    const short = Number(a.short_score || 0);
    const isLong = a.signal === 'LONG';
    const isShort = a.signal === 'SHORT';
    const candidate = a.candidate;
    const dominant = Math.max(long, short);
    const signalNode = $('signalValue');
    signalNode.textContent = isLong ? 'LONG' : isShort ? 'SHORT' : candidate === 'LONG' ? 'VIGILAR PISO' : candidate === 'SHORT' ? 'VIGILAR TECHO' : 'ESPERAR';
    signalNode.className = `signal-value ${isLong || candidate === 'LONG' ? 'long' : isShort || candidate === 'SHORT' ? 'short' : 'neutral'}`;
    $('signalKicker').textContent = isLong ? 'GIRO ALCISTA CONFIRMADO' : isShort ? 'GIRO BAJISTA CONFIRMADO' : candidate === 'LONG' ? 'ZONA DE POSIBLE AGOTAMIENTO' : candidate === 'SHORT' ? 'ZONA DE POSIBLE AGOTAMIENTO' : 'SIN CONFIRMACIÓN DIRECCIONAL';
    $('dominantScore').textContent = `${Math.round(dominant)}`;
    $('scoreRing').style.background = `conic-gradient(${isShort || (candidate === 'SHORT' && short >= long) ? 'var(--red)' : 'var(--cyan)'} ${dominant * 3.6}deg,#243648 0deg)`;
    $('phaseText').textContent = a.phase || 'Reuniendo información del mercado.';
    $('longScore').textContent = `${long.toFixed(1)} / 100`;
    $('shortScore').textContent = `${short.toFixed(1)} / 100`;
    $('longBar').style.width = `${Math.min(100, long)}%`;
    $('shortBar').style.width = `${Math.min(100, short)}%`;
    $('threshold').textContent = `${Number(a.threshold || 68).toFixed(1)} / 100`;
    $('rsi').textContent = a.rsi === undefined ? '—' : Number(a.rsi).toFixed(1);
    $('atr').textContent = a.atr_pct === undefined ? '—' : `${Number(a.atr_pct).toFixed(2)}%`;

    const reasons = Array.isArray(a.reasons) ? a.reasons : [];
    $('reasons').innerHTML = reasons.length
      ? reasons.map((r) => `<div class="reason-item"><span class="reason-check">↗</span><span>${escapeHtml(r)}</span></div>`).join('')
      : '<div class="reason-empty">No hay evidencia suficiente para inclinarse por un extremo.</div>';
    $('candidateText').textContent = isLong ? 'La presión y el giro coinciden: posible continuación alcista.' : isShort ? 'La presión y el giro coinciden: posible continuación bajista.' : candidate === 'LONG' ? 'Hay condiciones de piso, pero falta confirmación para alertar.' : candidate === 'SHORT' ? 'Hay condiciones de techo, pero falta confirmación para alertar.' : 'Esperando coincidencia entre precio, tiempo y cambio de impulso.';

    const eventKey = `${a.signal}:${a.last_candle_time}`;
    if ((isLong || isShort) && !alertedEvents.has(eventKey)) {
      alertedEvents.add(eventKey);
      if (alertedEvents.size > 80) alertedEvents.delete(alertedEvents.values().next().value);
      const message = `${a.signal} ${s.symbol}: ${fmtPrice(s.price)} USDT · puntuación ${isLong ? long.toFixed(1) : short.toFixed(1)}`;
      showToast(message);
      if (notifyAllowed && 'Notification' in window && Notification.permission === 'granted') {
        new Notification(`PRIMA-15 · ${a.signal}`, { body: message });
      }
    }

    const lp = a.prime_long || {};
    const sp = a.prime_short || {};
    $('longPrime').textContent = lp.elapsed ? `${lp.elapsed} → ${lp.nearest || '—'}` : '—';
    $('longPrimeCaption').textContent = lp.elapsed ? `${lp.elapsed} velas · primo más cercano ${lp.nearest} · diferencia ${lp.delta}` : 'Sin tramo bajista confirmado';
    $('shortPrime').textContent = sp.elapsed ? `${sp.elapsed} → ${sp.nearest || '—'}` : '—';
    $('shortPrimeCaption').textContent = sp.elapsed ? `${sp.elapsed} velas · primo más cercano ${sp.nearest} · diferencia ${sp.delta}` : 'Sin tramo alcista confirmado';
    renderPivots(a.pivots || []);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function renderPivots(pivots) {
    const body = $('pivotRows');
    if (!pivots.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-row">Aún no se han confirmado extremos.</td></tr>';
      return;
    }
    body.innerHTML = pivots.slice().reverse().map((p) => {
      const high = p.type === 'HIGH';
      const duration = p.duration_bars ? `${p.duration_bars} velas · ${(p.duration_bars * 15 / 60).toFixed(2)} h` : 'Punto inicial';
      const prime = p.nearest_prime ? `P${p.nearest_prime} ${p.prime_delta === 0 ? '· exacto' : `· ±${p.prime_delta}`}` : '—';
      return `<tr><td><span class="type-pill ${high ? 'type-high' : 'type-low'}">${high ? 'MÁXIMO' : 'MÍNIMO'}</span></td><td>${fmtPrice(p.price)}</td><td>${fmtTime(p.time)}</td><td>${duration}</td><td class="prime-cell">${prime}</td></tr>`;
    }).join('');
  }

  function humanizeCode(value) {
    return String(value || 'SIN DATO').replace(/_/g, ' ').toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
  }

  function metricCell(label, value) {
    return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  function renderElliott(elliott, hybrid) {
    const data = elliott || {};
    const frames = data.timeframes || {};
    const frameIds = { '1m': 'elliott1m', '15m': 'elliott15m', '1h': 'elliott1h', '4h': 'elliott4h' };
    const formatRule = (key) => ({
      wave_2_does_not_exceed_wave_1_origin: 'Onda 2 respeta origen',
      wave_3_exceeds_wave_1_end: 'Onda 3 supera onda 1',
      wave_3_is_not_shortest: 'Onda 3 no es la más corta',
      wave_4_does_not_overlap_wave_1: 'Onda 4 no solapa onda 1',
      all_five_legs_have_directional_progress: 'Cinco tramos direccionales'
    }[key] || humanizeCode(key));
    Object.entries(frameIds).forEach(([name, id]) => {
      const host = $(id);
      const frame = frames[name] || {};
      const count = frame.count || {};
      const legs = Array.isArray(count.legs) ? count.legs : [];
      const pivots = Array.isArray(count.pivots) ? count.pivots : [];
      const rules = count.hard_rules || {};
      const prime = count.prime_time_overlay || {};
      const directionClass = count.direction === 'UP' ? 'elliott-up' : count.direction === 'DOWN' ? 'elliott-down' : 'elliott-neutral';
      const countStatus = count.valid && !count.provisional ? 'IMPULSO ESTÁNDAR VÁLIDO' : count.provisional ? 'CONTEO PROVISIONAL' : 'SIN VALIDACIÓN';
      const legHtml = legs.length ? `<div class="elliott-leg-list">${legs.map((leg, index) => {
        const label = leg.label || `TRAMO ${index + 1}`;
        const sign = Number(leg.change_pct || 0) >= 0 ? '+' : '';
        const primeLabel = leg.nearest_prime ? `P${leg.nearest_prime} · Δ${leg.prime_delta}` : 'sin primo';
        return `<div class="elliott-leg"><b>${escapeHtml(label)}</b><span>${escapeHtml(leg.from || '')} → ${escapeHtml(leg.to || '')}</span><strong>${sign}${Number(leg.change_pct || 0).toFixed(3)}%</strong><small>${Number(leg.duration_bars || 0)} velas · ${escapeHtml(primeLabel)}</small></div>`;
      }).join('')}</div>` : '';
      const ruleHtml = Object.keys(rules).length ? `<div class="elliott-rules">${Object.entries(rules).map(([key, passed]) => `<span class="${passed ? 'rule-pass' : 'rule-fail'}">${passed ? '✓' : '×'} ${escapeHtml(formatRule(key))}</span>`).join('')}</div>` : '';
      const fibRows = count.guidelines ? Object.entries(count.guidelines).filter(([, value]) => value && typeof value === 'object').map(([key, value]) => `${humanizeCode(key)} ${Number(value.observed || 0).toFixed(3)} → ${value.nearest_fib ?? '—'}`).join(' · ') : '';
      const provisional = frame.provisional_pivot ? `Extremo en formación: ${frame.provisional_pivot.type} ${fmtPrice(frame.provisional_pivot.price)} (no contado como confirmado).` : 'Sin extremo provisional.';
      host.querySelector('.elliott-degree-content').innerHTML = `
        <div class="elliott-count-head"><strong class="${directionClass}">${escapeHtml(humanizeCode(count.pattern || 'NO_CLEAR_ELLIOTT_COUNT'))}</strong><span>${Number(count.score || 0).toFixed(0)}/100 · ${escapeHtml(countStatus)}</span></div>
        <div class="elliott-stats">${metricCell('VELAS PROCESADAS', Number(frame.candle_count || 0).toLocaleString('es-CL'))}${metricCell('HISTORIAL', `${Number(frame.history_days || 0).toFixed(1)} días`)}${metricCell('PIVOTES CONFIRMADOS', Number(frame.pivot_count_total || 0).toLocaleString('es-CL'))}${metricCell('UMBRAL ZIGZAG', frame.zigzag_threshold_pct == null ? '—' : `${Number(frame.zigzag_threshold_pct).toFixed(3)}%`)}</div>
        ${ruleHtml}${legHtml}
        <div class="elliott-prime-fit">Ajuste prima de las duraciones: <b>${Number(prime.fit_pct || 0).toFixed(1)}%</b> (${Number(prime.matched_legs || 0)}/${Number(prime.total_legs || 0)} tramos)</div>
        ${fibRows ? `<div class="elliott-fib">${escapeHtml(fibRows)}</div>` : ''}
        <p class="elliott-explain">${escapeHtml(count.interpretation || 'Aún no hay explicación disponible.')} ${escapeHtml(provisional)}</p>`;
    });
    const history = data.history || {};
    const synthesis = data.multi_degree_synthesis || {};
    const relation = hybrid || data.prime_elliott_relation || {};
    $('elliottHybrid').innerHTML = `<b>${escapeHtml(humanizeCode(relation.state || 'SIN_CONFLUENCIA'))}</b><span>${escapeHtml(relation.conclusion || synthesis.conclusion || 'Todavía no existe una lectura combinada.')}</span><small>Historial Elliott: ${Number(history['15m_candles_processed'] || 0).toLocaleString('es-CL')} velas 15m · ${Number(history['1h_candles_aggregated'] || 0)} velas 1h · ${Number(history['4h_candles_aggregated'] || 0)} velas 4h · Sesgo multigrado: ${escapeHtml(synthesis.direction || 'UNDETERMINED')}</small>`;
    const notes = Array.isArray(data.limitations) ? data.limitations : [];
    $('elliottCaveat').textContent = notes.slice(0, 3).join(' ')
      || 'Conteos provisionales; no se fuerzan etiquetas cuando la estructura no es suficientemente clara.';
  }

  function updateWaveDiagnostics(s) {
    const a = s.algorithm_analysis || s.analysis || {};
    const dimensions = a.wave_dimensions || {};
    const temporal = dimensions.temporal || {};
    const amplitude = dimensions.amplitude || {};
    const behavior = dimensions.behavior || {};
    const synthesis = dimensions.synthesis || {};
    renderElliott(a.elliott_wave || s.elliott_wave || {}, a.elliott_prime_synthesis || s.elliott_prime_synthesis || {});

    $('temporalPhase').textContent = humanizeCode(temporal.bias || temporal.status);
    $('temporalMetrics').innerHTML = [
      metricCell('ESCALAS EN ZONA BAJA', `${Number(temporal.low_scale_count || 0)} / ${Number(temporal.scale_count || 0)}`),
      metricCell('ESCALAS EN ZONA ALTA', `${Number(temporal.high_scale_count || 0)} / ${Number(temporal.scale_count || 0)}`),
      metricCell('AJUSTE PRIMO DE TRAMOS', `${Number(temporal.prime_fit_pct || 0).toFixed(1)} / 100`),
      metricCell('TIEMPO DESDE ÚLTIMO EXTREMO', temporal.elapsed_from_last_high !== null && temporal.elapsed_from_last_high !== undefined ? `${temporal.elapsed_from_last_high} velas desde máximo · ${temporal.elapsed_from_last_low ?? '—'} desde mínimo` : '—')
    ].join('');
    $('temporalExplanation').textContent = temporal.explanation || 'Sin lectura temporal disponible.';
    const scaleRows = Array.isArray(temporal.scales) ? temporal.scales : [];
    $('temporalChips').innerHTML = scaleRows.slice(0, 18).map((row) => {
      const cls = row.near_low ? 'near-low' : row.near_high ? 'near-high' : '';
      const label = `P${row.prime} · P=${Number(row.p_close).toFixed(2)}`;
      return `<span class="dimension-chip ${cls}" title="${Number(row.minutes)} minutos">${escapeHtml(label)}</span>`;
    }).join('') || '<span class="dimension-muted">Todavía no hay escalas suficientes.</span>';

    const latestLeg = amplitude.latest_leg || {};
    $('amplitudePhase').textContent = humanizeCode(amplitude.phase || amplitude.status);
    $('amplitudeMetrics').innerHTML = [
      metricCell('ÚLTIMA ONDA', latestLeg.direction ? `${latestLeg.direction} · ${fmtPct(latestLeg.amplitude_pct, 4)}` : '—'),
      metricCell('DURACIÓN', latestLeg.duration_bars ? `${latestLeg.duration_bars} velas · ${latestLeg.duration_minutes} min` : '—'),
      metricCell('MISMO SENTIDO', amplitude.same_direction_ratio !== null && amplitude.same_direction_ratio !== undefined ? `${Number(amplitude.same_direction_ratio).toFixed(3)}×` : '—'),
      metricCell('RETROCESO', amplitude.retracement_ratio !== null && amplitude.retracement_ratio !== undefined ? `${(Number(amplitude.retracement_ratio) * 100).toFixed(1)}%` : '—')
    ].join('');
    $('amplitudeExplanation').textContent = amplitude.explanation || 'Sin lectura de amplitud disponible.';
    const amplitudeChips = [];
    if (amplitude.same_direction_prime_ratio) {
      amplitudeChips.push(`Ratio observado ${amplitude.same_direction_prime_ratio.observed_ratio}×`);
      amplitudeChips.push(`Ratio primo ${amplitude.same_direction_prime_ratio.nearest_prime_ratio}×`);
      amplitudeChips.push(`Desvío ${amplitude.same_direction_prime_ratio.error_pct}%`);
    }
    if (amplitude.retracement_label) amplitudeChips.push(humanizeCode(amplitude.retracement_label));
    if (amplitude.developing_leg) amplitudeChips.push(`Tramo en formación ${amplitude.developing_leg.direction} ${fmtPct(amplitude.developing_leg.amplitude_pct, 4)}`);
    $('amplitudeChips').innerHTML = amplitudeChips.map((item) => `<span class="dimension-chip">${escapeHtml(item)}</span>`).join('') || '<span class="dimension-muted">Faltan ondas comparables.</span>';

    const fingerprint = behavior.fingerprint || {};
    $('behaviorPhase').textContent = humanizeCode(behavior.phase || behavior.status);
    $('behaviorMetrics').innerHTML = [
      metricCell('RETORNO EN 13 VELAS', fmtPct(fingerprint.net_return_pct, 4)),
      metricCell('EFICIENCIA DE TRAYECTORIA', fingerprint.efficiency === undefined ? '—' : Number(fingerprint.efficiency).toFixed(3)),
      metricCell('VELAS ALCISTAS', fingerprint.up_fraction === undefined ? '—' : `${(Number(fingerprint.up_fraction) * 100).toFixed(1)}%`),
      metricCell('SIMILITUD MEDIA', `${Number(behavior.average_analogy_similarity_pct || 0).toFixed(1)}% · no es probabilidad`)
    ].join('');
    $('behaviorExplanation').textContent = behavior.explanation || 'Sin huella histórica disponible.';
    const analogs = behavior.analogs || {};
    $('behaviorHorizons').innerHTML = [4, 8, 12].map((h) => {
      const row = analogs[String(h)] || {};
      const direction = row.direction || 'SIN MUESTRA';
      const cls = direction === 'UP' ? 'analog-up' : direction === 'DOWN' ? 'analog-down' : '';
      const ret = row.weighted_return_pct === null || row.weighted_return_pct === undefined ? '—' : fmtPct(row.weighted_return_pct, 4);
      return `<div class="analog-horizon ${cls}"><span>${h * 15} MIN</span><strong>${escapeHtml(direction)}</strong><small>${Number(row.sample_count || 0)} patrones · ${ret}</small></div>`;
    }).join('');

    const observations = Array.isArray(synthesis.observations) ? synthesis.observations : [];
    const conflicts = Array.isArray(synthesis.conflicts) ? synthesis.conflicts : [];
    const adjustments = a.dimension_adjustments || {};
    const adjustmentReasons = Array.isArray(adjustments.reasons) ? adjustments.reasons : [];
    const adjustmentText = adjustmentReasons.length ? `Ajustes técnicos: ${adjustmentReasons.join(' ')}` : 'Las dimensiones no modifican el puntaje direccional en esta lectura.';
    $('dimensionSynthesis').innerHTML = `<strong>${escapeHtml(humanizeCode(synthesis.interpretation))}</strong><span>${escapeHtml(observations.join(' '))} ${escapeHtml(conflicts.join(' '))} ${escapeHtml(adjustmentText)} ${escapeHtml(synthesis.warning || '')}</span>`;

    const wick = a.wick_analysis || {};
    const lower = wick.lower || {};
    const upper = wick.upper || {};
    $('wickOverall').textContent = humanizeCode(wick.classification);
    $('wickLowerClass').textContent = humanizeCode(lower.classification);
    $('wickUpperClass').textContent = humanizeCode(upper.classification);
    $('wickLowerMetrics').innerHTML = [
      metricCell('MECHA / RANGO', `${Number(wick.lower_wick_pct_range || 0).toFixed(1)}%`),
      metricCell('MECHA / CUERPO', `${Number(wick.lower_wick_to_body || 0).toFixed(2)}×`),
      metricCell('ESCALAS BAJAS', `${Number(wick.bottom_scale_count || 0)}`),
      metricCell('P15 DEL CIERRE', wick.p_15m === undefined ? '—' : Number(wick.p_15m).toFixed(3))
    ].join('');
    $('wickUpperMetrics').innerHTML = [
      metricCell('MECHA / RANGO', `${Number(wick.upper_wick_pct_range || 0).toFixed(1)}%`),
      metricCell('MECHA / CUERPO', `${Number(wick.upper_wick_to_body || 0).toFixed(2)}×`),
      metricCell('ESCALAS ALTAS', `${Number(wick.top_scale_count || 0)}`),
      metricCell('P15 DEL CIERRE', wick.p_15m === undefined ? '—' : Number(wick.p_15m).toFixed(3))
    ].join('');
    const wickScales = Array.isArray(wick.scale_readings) ? wick.scale_readings : [];
    $('wickLowerScales').innerHTML = wickScales.filter((row) => row.near_low || Number(row.p_low) <= 0.18).slice(0, 12)
      .map((row) => `<span class="dimension-chip near-low">P${row.window} · mecha P=${Number(row.p_low).toFixed(2)}</span>`).join('')
      || '<span class="dimension-muted">La mecha no entra en zona inferior de las escalas disponibles.</span>';
    $('wickUpperScales').innerHTML = wickScales.filter((row) => row.near_high || Number(row.p_high) >= 0.82).slice(0, 12)
      .map((row) => `<span class="dimension-chip near-high">P${row.window} · mecha P=${Number(row.p_high).toFixed(2)}</span>`).join('')
      || '<span class="dimension-muted">La mecha no entra en zona superior de las escalas disponibles.</span>';
    const renderEvidence = (reasons, blockers) => {
      const reasonHtml = (reasons || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
      const blockerHtml = (blockers || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
      return { reasonHtml, blockerHtml };
    };
    const lowerEvidence = renderEvidence(lower.reasons, lower.blockers);
    const upperEvidence = renderEvidence(upper.reasons, upper.blockers);
    $('wickLowerReasons').innerHTML = lowerEvidence.reasonHtml || '<li>No hay confirmaciones positivas.</li>';
    $('wickUpperReasons').innerHTML = upperEvidence.reasonHtml || '<li>No hay confirmaciones positivas.</li>';
    $('wickLowerBlockers').innerHTML = lowerEvidence.blockerHtml ? `<b>Qué falta:</b><ul>${lowerEvidence.blockerHtml}</ul>` : '<b>Sin bloqueadores pendientes.</b>';
    $('wickUpperBlockers').innerHTML = upperEvidence.blockerHtml ? `<b>Qué falta:</b><ul>${upperEvidence.blockerHtml}</ul>` : '<b>Sin bloqueadores pendientes.</b>';
  }

  function updateLearning(s) {
    const l = s.learning || {};
    const b = s.backtest || {};
    $('learningWin').textContent = l.win_rate === null || l.win_rate === undefined ? '—' : `${Number(l.win_rate).toFixed(1)}%`;
    $('learningSamples').textContent = Number(l.samples || 0).toLocaleString('es-CL');
    $('learningAvg').textContent = l.avg_return === null || l.avg_return === undefined ? '—' : fmtPct(l.avg_return, 3);
    $('learningCopy').textContent = Number(l.samples || 0) >= 20
      ? `El umbral técnico actual se ajusta a ${Number(l.threshold).toFixed(1)} puntos usando los últimos resultados direccionales de una hora.`
      : `Aún faltan observaciones para adaptar el umbral técnico. La calibración comienza con 20 resultados; ahora usa el umbral base.`;
    const a = s.adaptive_learning || {};
    const weights = a.dimension_weights || { temporal: 1, amplitude: 1, behavior: 1 };
    const modelRows = Array.isArray(a.model_reliability) ? a.model_reliability : [];
    const modelText = modelRows.length
      ? modelRows.slice(0, 3).map((m) => `${escapeHtml(m.model.split('/').pop())}: ${Number(m.win_rate_pct).toFixed(1)}% (${m.samples})`).join(' · ')
      : 'Modelos: reuniendo muestras comparables';
    const tp = a.tp_comparison || {};
    const tp20 = tp['0.20'] && tp['0.20'].mean_net_wallet_points_pct !== null
      ? `${Number(tp['0.20'].mean_net_wallet_points_pct).toFixed(3)}%` : 'sin muestra';
    const tp30 = tp['0.30'] && tp['0.30'].mean_net_wallet_points_pct !== null
      ? `${Number(tp['0.30'].mean_net_wallet_points_pct).toFixed(3)}%` : 'sin muestra';
    const accuracyText = a.directional_accuracy_pct === null || a.directional_accuracy_pct === undefined
      ? '—' : `${Number(a.directional_accuracy_pct).toFixed(1)}%`;
    const avgText = a.avg_directional_return_pct === null || a.avg_directional_return_pct === undefined
      ? '—' : fmtPct(a.avg_directional_return_pct, 3);
    const trendText = a.trend_accuracy_pct === null || a.trend_accuracy_pct === undefined
      ? '—' : `${Number(a.trend_accuracy_pct).toFixed(1)}% (${Number(a.trend_samples || 0)} muestras)`;
    $('adaptiveSummary').innerHTML = `<strong>${a.status === 'ADAPTIVE' ? 'AUTOAJUSTE ACTIVO' : 'FASE DE APRENDIZAJE'}</strong><div class="adaptive-line"><span>Predicciones con resultado a 1 h</span><span>${Number(a.sample_count || 0)} / ${Number(a.min_samples || 30)}</span></div><div class="adaptive-line"><span>Exactitud direccional</span><span>${accuracyText}</span></div><div class="adaptive-line"><span>Retorno direccional medio</span><span>${avgText}</span></div><div class="adaptive-line"><span>Éxito histórico de continuidad</span><span>${trendText}</span></div><div class="adaptive-line"><span>Continuidad mínima adaptada</span><span>${Number(a.trend_agreement_min_pct ?? 60).toFixed(1)}%</span></div><div class="adaptive-line"><span>Take Profit activo</span><span>+${Number(a.take_profit_target_pct ?? 0.20).toFixed(2)}%</span></div><div class="adaptive-line"><span>Comparación TP 0,20 / 0,30</span><span>${tp20} / ${tp30}</span></div><div class="adaptive-weights"><span>Tiempo ×${Number(weights.temporal || 1).toFixed(3)}</span><span>Amplitud ×${Number(weights.amplitude || 1).toFixed(3)}</span><span>Comportamiento ×${Number(weights.behavior || 1).toFixed(3)}</span></div><div class="adaptive-line"><span>Rendimiento comparado por modelo</span><span>${modelText}</span></div><div class="adaptive-line"><span>Reglas</span><span>Solo datos futuros cerrados · parámetros acotados · sin reescritura de código</span></div>`;
    $('backtestSummary').textContent = Number(b.samples || 0)
      ? `${b.samples} señales · ${Number(b.win_rate).toFixed(1)}% acierto · PF ${b.profit_factor === null ? '—' : b.profit_factor}`
      : 'Aún sin muestra suficiente';
  }

  function renderFastEntry(fast, crossMarket, gateMode = 'ADVISORY') {
    const f = fast || {};
    const status = String(f.status || 'WAITING');
    const ready = status === 'READY';
    $('fastEntryStatus').textContent = ready ? 'ANÁLISIS 1M ACTIVO' : status === 'INSUFFICIENT_DATA' ? 'FALTA HISTÓRICO 1M' : 'REUNIENDO DATOS';
    $('fastEntryStatus').className = `ai-status ${ready ? 'is-ok' : ''}`;
    const candidate = f.candidate_side || 'NINGUNO';
    $('fastEntryCandidate').textContent = candidate === 'LONG' ? 'CANDIDATO LONG' : candidate === 'SHORT' ? 'CANDIDATO SHORT' : 'SIN CANDIDATO';
    $('fastEntryCandidate').className = candidate === 'LONG' ? 'fast-candidate-long' : candidate === 'SHORT' ? 'fast-candidate-short' : '';
    const book = f.orderbook || {};
    const flow = f.trade_flow || {};
    const pressure = f.directional_pressure || {};
    const cross = (crossMarket && crossMarket.assets) || (f.cross_market && f.cross_market.assets) || {};
    const crossText = ['BTCUSDT', 'ETHUSDT'].map((sym) => {
      const row = cross[sym] || {};
      const roc = row.roc_5m_pct;
      return `${sym.replace('USDT', '')}: ${roc === undefined || roc === null ? 'sin dato' : fmtPct(roc, 3)}`;
    }).join(' · ');
    const opportunity = (side) => (f.opportunities || {})[side] || {};
    const renderSide = (side) => {
      const item = opportunity(side);
      const hist = item.historical || {};
      const metrics = [
        metricCell('TP PRIMERO · HISTÓRICO', hist.tp_first_rate_pct === undefined ? '—' : `${Number(hist.tp_first_rate_pct).toFixed(1)}%`),
        metricCell('WILSON 80%', hist.wilson_lower_80_pct === undefined ? '—' : `${Number(hist.wilson_lower_80_pct).toFixed(1)}%`),
        metricCell('MUESTRAS SEPARADAS', `${Number(hist.sample_count || 0)} · ${Number(hist.tp_first_count || 0)} TP`),
        metricCell('MEDIANA HASTA TP', hist.median_minutes_to_tp === null || hist.median_minutes_to_tp === undefined ? '—' : `${Number(hist.median_minutes_to_tp).toFixed(1)} min`),
        metricCell('PRESIÓN ACTUAL', item.live_confirmation_pct === undefined ? '—' : `${Number(item.live_confirmation_pct).toFixed(1)} / 100`),
        metricCell('CALIDAD DIRECCIONAL', item.directional_quality_score === undefined ? '—' : `${Number(item.directional_quality_score).toFixed(1)} / 100`),
        metricCell('VENTAJA LONG/SHORT', item.directional_edge_gap_pct === undefined ? '—' : `${Number(item.directional_edge_gap_pct).toFixed(1)} puntos`),
        metricCell('OBSTÁCULO', item.runway?.obstacle_source ? item.runway.obstacle_source : 'sin obstáculo cercano'),
        metricCell('ESPACIO AL OBSTÁCULO', item.runway?.distance_to_obstacle_pct === null || item.runway?.distance_to_obstacle_pct === undefined ? '—' : `${Number(item.runway.distance_to_obstacle_pct).toFixed(3)}%`)
      ].join('');
      const blockers = Array.isArray(item.blockers) ? item.blockers : [];
      const reasons = blockers.length ? blockers.map((v) => `<li>${escapeHtml(v)}</li>`).join('') : '<li>Todos los filtros de entrada rápida están satisfechos.</li>';
      const eligible = Boolean(item.eligible);
      const advisory = String(gateMode || 'ADVISORY').toUpperCase() !== 'ENFORCED';
      const gateLabel = eligible ? 'APTO' : advisory ? 'SOLO AVISO' : item.watch_candidate ? 'VIGILAR' : 'BLOQUEADO';
      $(`fast${side === 'LONG' ? 'Long' : 'Short'}Gate`).textContent = gateLabel;
      $(`fast${side === 'LONG' ? 'Long' : 'Short'}Gate`).className = `fast-gate ${eligible ? 'fast-gate-ok' : item.watch_candidate ? 'fast-gate-watch' : ''}`;
      $(`fast${side === 'LONG' ? 'Long' : 'Short'}Metrics`).innerHTML = metrics;
      $(`fast${side === 'LONG' ? 'Long' : 'Short'}Blockers`).innerHTML = reasons;
    };
    renderSide('LONG');
    renderSide('SHORT');
    $('fastEntrySummary').textContent = ready
      ? `${Number(f.history_bars || 0)} velas 1m · ${Number(f.historical_sample_count || 0)} referencias · objetivo +${Number(f.target_pct || 0.20).toFixed(2)}% · horizonte ${Number(f.outcome_horizon_minutes || 20)} min · ${Number(fast?.rules?.minimum_tp_first_rate_pct || 62).toFixed(0)}% TP-first mínimo`
      : (f.reasons || ['Todavía no hay histórico suficiente para evaluar el primer ingreso.']).join(' ');
    const pressureText = `Presión LONG ${Number(pressure.long_score || 0).toFixed(1)} · SHORT ${Number(pressure.short_score || 0).toFixed(1)}`;
    const relative = f.cross_market_diagnostics || {};
    const relativeText = relative.render_relative_strength_5m_pct === undefined ? 'fuerza relativa RENDER/BTC-ETH: sin dato' : `fuerza relativa RENDER vs BTC/ETH: ${fmtPct(relative.render_relative_strength_5m_pct, 3)} (${relative.relative_reading || '—'})`;
    const bookText = `Spread ${book.spread_bps === null || book.spread_bps === undefined ? '—' : `${Number(book.spread_bps).toFixed(2)} bps`} · imbalance libro ${book.imbalance === null || book.imbalance === undefined ? '—' : Number(book.imbalance).toFixed(3)}`;
    const flowText = `Taker imbalance ${flow.taker_imbalance === null || flow.taker_imbalance === undefined ? '—' : Number(flow.taker_imbalance).toFixed(3)} (${Number(flow.trade_count || 0)} trades)`;
    const gateText = String(gateMode).toUpperCase() === 'ENFORCED'
      ? 'El filtro está en modo estricto y puede bloquear entradas.'
      : 'Fast-entry es informativo y no bloquea por sí solo una decisión LONG/SHORT válida del comité.';
    $('fastMarketFoot').textContent = `${pressureText} · ${bookText} · ${flowText} · Cruce 5m: ${crossText} · ${relativeText}. ${gateText} La barrera adversa de +/−${Number(f.adverse_path_barrier_pct || 0.12).toFixed(2)}% solo etiqueta el backtest; no es un stop automático.`;
  }

  function renderSignals(rows) {
    const body = $('signalRows');
    if (!rows || !rows.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty-row">Todavía no se han emitido alertas confirmadas.</td></tr>';
      return;
    }
    body.innerHTML = rows.map((r) => {
      const isLong = r.side === 'LONG';
      const outcome = r.return_pct === null || r.return_pct === undefined
        ? '<span class="neutral-text">Pendiente</span>'
        : `<span class="${Number(r.return_pct) >= 0 ? 'positive-text' : 'negative-text'}">${fmtPct(r.return_pct, 3)}</span>`;
      const prime = r.prime_nearest ? `P${r.prime_nearest} (${r.prime_elapsed})` : '—';
      return `<tr><td>${fmtTime(r.bar_time)}</td><td class="${isLong ? 'side-long' : 'side-short'}">${r.side}</td><td>${fmtPrice(r.entry)}</td><td>${Number(r.score).toFixed(1)}</td><td class="prime-cell">${prime}</td><td>${outcome}</td></tr>`;
    }).join('');
  }

  function updateAI(s) {
    const ai = s.ai || {};
    const status = ai.status || 'waiting';
    const statusLabels = {
      ok: 'OPENROUTER ACTIVO',
      waiting: 'ESPERANDO MODELOS',
      not_configured: 'API NO CONFIGURADA',
      error: 'ERROR DE EVALUACIÓN'
    };
    $('aiStatus').textContent = statusLabels[status] || 'ESTADO IA';
    $('aiStatus').className = `ai-status ${status === 'ok' ? 'is-ok' : status === 'error' ? 'is-error' : ''}`;
    const decision = ai.decision || 'WAIT';
    $('aiDecision').textContent = decision;
    $('aiDecision').className = `ai-decision ${decision === 'LONG' ? 'long' : decision === 'SHORT' ? 'short' : 'neutral'}`;
    $('aiConfidence').textContent = `Convicción: ${Number(ai.confidence || 0).toFixed(0)} / 100`;
    $('aiConsensus').textContent = ai.consensus_pct === undefined ? '—' : `${Number(ai.consensus_pct).toFixed(0)}%`;
    const votes = Array.isArray(ai.votes) ? ai.votes : [];
    const required = Number(ai.required_votes || ai.total_models || (ai.models || []).length || 1);
    const backupUsed = Array.isArray(ai.fallback_used_models) ? ai.fallback_used_models : [];
    $('aiVotes').textContent = votes.length
      ? `${votes.length} voto(s) válido(s) de ${required} requerido(s)${backupUsed.length ? ' · respaldo activado' : ''}`
      : 'Sin respuestas válidas de modelos';
    const primaryNames = (ai.models || []).join(', ') || '—';
    const backupNames = (ai.fallback_models || []).join(', ');
    $('aiModels').textContent = `Principales: ${primaryNames}${backupNames ? ` · Respaldos: ${backupNames}` : ''}`;
    $('aiUpdated').textContent = ai.updated_at ? `Última evaluación: ${fmtTime(ai.updated_at)}` : 'Última evaluación: —';
    const basis = ai.decision_basis ? `Base: ${humanizeCode(ai.decision_basis)}.` : '';
    const trendFact = ai.trend_direction ? ` Tendencia 3/5/8: ${ai.trend_direction} (${Number(ai.trend_agreement_pct || 0).toFixed(1)}%).` : '';
    const anomalyFact = ai.lower_rejection_confirmed ? ' Rechazo inferior confirmado: prioridad al primer LLM.' : ai.upper_rejection_confirmed ? ' Rechazo superior confirmado: prioridad al primer LLM.' : '';
    $('aiSummary').textContent = `${ai.summary || 'Esperando análisis de los modelos.'} ${basis}${trendFact}${anomalyFact}`.trim();
    const failureText = Array.isArray(ai.errors) && ai.errors.length ? ` · Fallas/respaldos: ${ai.errors.join(' | ')}` : '';
    const usedText = backupUsed.length ? ` · Respaldo que respondió: ${backupUsed.join(', ')}` : '';
    $('aiRisk').textContent = `${ai.risk || 'Sin evaluación disponible.'}${usedText}${failureText}`;
    const cycle = s.committee_cycle || {};
    const intervalMinutes = Number(cycle.interval_minutes || 5);
    const dailyRuns = Number(cycle.runs_today ?? s.committee_runs_today ?? 0);
    const dailyLimit = Number(cycle.daily_limit ?? s.committee_daily_limit ?? 20);
    const remaining = Number(cycle.remaining_today ?? Math.max(0, dailyLimit - dailyRuns));
    const gateMode = cycle.fast_entry_gate_mode || ai.fast_entry_gate_mode || 'ADVISORY';
    $('committeeCycle').textContent = `Ciclo LLM cada ${intervalMinutes.toFixed(0)} min · comités UTC: ${dailyRuns}/${dailyLimit} · restantes: ${remaining} · filtro fast-entry: ${gateMode === 'ENFORCED' ? 'estricto' : 'orientativo'} · nuevo comité inmediato después de Take Profit.`;
    $('aiVotesList').innerHTML = votes.length ? votes.map((v) => {
      const side = v.decision || 'WAIT';
      const klass = side === 'LONG' ? 'long' : side === 'SHORT' ? 'short' : 'neutral';
      const source = v.source === 'respaldo' ? ' · RESPALDO' : '';
      const role = v.analysis_role === 'exception' ? 'EXCEPCIÓN / RECHAZOS' : 'CONTINUIDAD';
      const mode = v.assessment_mode === 'typed_probabilities' ? 'clasificación tipada' : v.assessment_mode === 'textual_basis' ? 'explicación textual' : 'formato de respuesta';
      const coverage = Number(v.evidence_coverage_pct || 0);
      const evidence = v.evidence_assessments && typeof v.evidence_assessments === 'object' ? v.evidence_assessments : {};
      const evidenceLabels = {
        market: 'Mercado 24h', cross_market: 'Confirmación BTC/ETH', raw_candles: 'Velas OHLCV', engine: 'Motor técnico', policy: 'Política activa',
        temporal: 'Dimensión temporal', prime: 'Resonancia prima', amplitude: 'Amplitud',
        behavior: 'Huella histórica', wick: 'Mechas y rechazos', trend: 'Continuidad 3/5/8',
        signals: 'Señales recientes', backtest: 'Backtest', prior_memory: 'Memoria LLM',
        portfolio: 'Wallet simulada', learning: 'Aprendizaje walk-forward',
        adaptive_profile: 'Perfil adaptativo', elliott: 'Conteo Elliott multigrado',
        elliott_prime: 'Relación Elliott / primos'
      };
      const evidenceHtml = Object.entries(evidence).map(([key, item]) => {
        const reading = item && typeof item === 'object' ? item.reading : item;
        const basis = item && typeof item === 'object' ? item.basis : '';
        return `<small class="ai-evidence-item"><b>${escapeHtml(evidenceLabels[key] || key)}:</b> ${escapeHtml(reading || '—')}${basis ? ` · ${escapeHtml(basis)}` : ''}</small>`;
      }).join('');
      return `<div class="ai-vote"><div><b>${escapeHtml(v.model || v.requested_model || 'Modelo')}${source}</b><span class="ai-vote-side ${klass}">${escapeHtml(side)}</span></div><small>${role} · ${mode} · cobertura ${coverage.toFixed(0)}%</small><span>${Number(v.confidence || 0).toFixed(0)}/100 · ${escapeHtml(v.summary || '')}</span>${evidenceHtml}</div>`;
    }).join('') : '';
    const audit = ai.context_audit || {};
    const manifest = audit.manifest || {};
    const counts = manifest.computed_detail_counts || {};
    $('aiContextAudit').textContent = audit.payload_bytes
      ? `Snapshot común enviado a cada modelo: ${(Number(audit.payload_bytes) / 1024).toFixed(1)} KB · ${Number(manifest.raw_candles_sent || 0)} velas 15m crudas · Elliott procesó ${Number(manifest.elliott_15m_history_processed || 0)} velas 15m y ${((manifest.elliott_timeframes_sent || []).join('/')) || 'sin grados'} · ${Number(manifest.fast_1m_candles_sent || 0)} velas 1m · BTC/ETH: ${(manifest.cross_market_assets_sent || []).join(', ') || 'sin dato'} · ${Number(counts.temporal_scale_readings_total || 0)} escalas temporales · ${Number(counts.amplitude_legs_total || 0)} ondas · ${Number(counts.wick_scale_readings_total || 0)} lecturas de mecha · ${Number(counts.behavior_cases_total || 0)} analogías · ${Number(counts.pivots_total || 0)} pivotes · ${Number(manifest.portfolio_history_rows_sent || 0)} operaciones previas · ${Number(manifest.backtest_examples_sent || 0)} ejemplos de backtest · ${Number(manifest.recent_signal_examples_sent || 0)} señales · ${Number(manifest.prior_ai_predictions_with_outcomes_sent || 0)} decisiones previas con outcomes · ${Number(manifest.adaptive_profile_fields_sent?.length || 0)} campos adaptativos · ${Number(manifest.required_evidence_count || 0)} familias obligatorias. ${audit.successful_model_responses || 0} respuestas. SHA-256 común: ${(audit.payload_sha256 || '').slice(0, 16)}…`
      : 'Aún no existe auditoría del contexto enviado.';
  }


  function updatePortfolio(s) {
    const p = s.portfolio || {};
    const ai = s.ai || {};
    $('walletBalance').textContent = `${Number(p.wallet_balance_pct ?? 100).toFixed(3)}%`;
    $('walletEquity').textContent = `${Number(p.equity_pct ?? p.wallet_balance_pct ?? 100).toFixed(3)}%`;
    $('walletReturn').textContent = fmtPct(p.total_return_pct ?? 0, 3);
    $('walletReturn').className = Number(p.total_return_pct || 0) >= 0 ? 'positive-text' : 'negative-text';
    $('walletFees').textContent = `${Number(p.total_fees_pct || 0).toFixed(3)}%`;
    $('positionTrades').textContent = Number(p.total_closed || 0).toLocaleString('es-CL');
    $('positionWinRate').textContent = p.win_rate_pct === null || p.win_rate_pct === undefined ? '—' : `${Number(p.win_rate_pct).toFixed(1)}%`;

    const pos = p.open_position;
    const side = pos ? pos.side : 'SIN POSICIÓN';
    $('positionSide').textContent = side;
    $('positionSide').className = `position-side ${side === 'LONG' ? 'long' : side === 'SHORT' ? 'short' : 'neutral'}`;
    $('positionOpened').textContent = pos
      ? `Abierta: ${fmtTime(pos.opened_at)} · exposición ${Number(p.position_size_pct || 100).toFixed(0)}% · TP +${Number(pos.take_profit_target_pct || p.take_profit_target_pct || 0.20).toFixed(2)}%`
      : 'No hay una operación simulada abierta.';
    if (pos) {
      $('positionReturn').textContent = fmtPct(pos.price_return_pct, 3);
      $('positionReturn').className = Number(pos.price_return_pct) >= 0 ? 'positive-text' : 'negative-text';
      $('positionWalletPnl').textContent = fmtPct(pos.unrealized_wallet_pnl_pct, 3);
      $('positionWalletPnl').className = Number(pos.unrealized_wallet_pnl_pct) >= 0 ? 'positive-text' : 'negative-text';
    } else {
      $('positionReturn').textContent = '—';
      $('positionWalletPnl').textContent = '—';
      $('positionReturn').className = '';
      $('positionWalletPnl').className = '';
    }

    const event = ai.position_event || {};
    const eventNames = {
      OPENED: `${event.reopened_after_take_profit ? 'Take Profit cerrado; ' : ''}nueva posición ${event.side || ''} abierta tras evaluación LLM actualizada. Coste aplicado solo en esta apertura: ${Number(event.opening_fee_rate_pct || p.opening_fee_rate_pct || 0.035).toFixed(3)}%.`,
      REVERSED: `${event.reopened_after_take_profit ? 'Take Profit anterior cerrado; ' : ''}se cerró la posición anterior y se abrió ${event.side || 'la dirección contraria'} según el nuevo consenso. El coste de apertura se descontó una sola vez para la nueva posición.`,
      HELD: `El comité mantiene ${event.side || 'la posición'}; no se aplicó un nuevo coste de apertura.`,
      TAKE_PROFIT_CLOSED: `Take Profit automático: posición cerrada al alcanzar +${Number(event.price_return_pct || 0).toFixed(3)}%. No se cobró comisión de cierre.`,
      HOLD_NO_VALID_VOTE: 'No hubo dos respuestas LLM válidas. Se conserva la posición actual y no se abre otra.',
      OPEN_BLOCKED: event.reason || 'No se pudo abrir la posición simulada.',
      NO_PRICE: event.reason || 'Esperando un precio válido.'
    };
    $('positionEvent').textContent = ai.closed_by_take_profit && !ai.reentry_after_take_profit
      ? `Take Profit cerrado (+${Number(ai.closed_by_take_profit.price_return_pct || 0).toFixed(3)}%). Se solicitó un comité nuevo, pero no hubo consenso direccional válido: la wallet permanece FLAT hasta la siguiente evaluación.`
      : (eventNames[event.event] || (
          ai.status === 'ok'
            ? (ai.policy_reason || 'Esperando la siguiente decisión del comité LLM.')
            : 'Sin quórum LLM válido: no se modifica la posición simulada.'
        ));
    $('portfolioStatus').textContent = pos ? `POSICIÓN ${pos.side} ABIERTA` : 'SIN POSICIÓN ABIERTA';
    $('portfolioStatus').className = `ai-status ${pos ? 'is-ok' : ''}`;

    const history = Array.isArray(p.history) ? p.history : [];
    $('positionRows').innerHTML = history.length ? history.map((t) => {
      const net = Number(t.net_wallet_return_pct || 0);
      const price = Number(t.price_return_pct || 0);
      return `<tr><td>${fmtTime(t.closed_at)}</td><td class="${t.side === 'LONG' ? 'side-long' : 'side-short'}">${escapeHtml(t.side)}</td><td class="${price >= 0 ? 'positive-text' : 'negative-text'}">${fmtPct(price, 3)}</td><td class="${net >= 0 ? 'positive-text' : 'negative-text'}">${fmtPct(net, 3)}</td><td>${net >= 0 ? '<span class="positive-text">GANANCIA</span>' : '<span class="negative-text">PÉRDIDA</span>'}</td></tr>`;
    }).join('') : '<tr><td colspan="5" class="empty-row">Todavía no hay operaciones cerradas.</td></tr>';
  }

  function updateMarket(s) {
    $('price').textContent = fmtPrice(s.price);
    $('candleCount').textContent = Number(s.candle_count || 0).toLocaleString('es-CL');
    if (s.change_24h) {
      const change = Number(s.change_24h.change_pct || 0);
      $('change24h').textContent = `${fmtPct(change)} en 24 h`;
      $('change24h').className = `price-change ${change >= 0 ? 'positive' : 'negative'}`;
      $('high24h').textContent = fmtPrice(s.change_24h.high);
      $('low24h').textContent = fmtPrice(s.change_24h.low);
    }
    $('lastUpdate').textContent = `Última lectura: ${fmtClock(s.updated_at)}`;
    $('dataSource').textContent = s.source || 'Fuente: Spot REST · solo lectura';
    const chartSource = $('chartSource');
    if (chartSource) chartSource.textContent = `${s.source || 'Fuente Spot'} · velas de 15 minutos`;
  }

  function drawChart(s) {
    const canvas = $('priceChart');
    const wrap = canvas.parentElement;
    const rect = wrap.getBoundingClientRect();
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const width = Math.max(300, rect.width);
    const height = Math.max(180, rect.height);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    const candles = s.candles || [];
    $('chartEmpty').style.display = candles.length ? 'none' : 'grid';
    if (candles.length < 2) return;

    const left = 8, right = 64, top = 13, bottom = 25;
    const plotW = width - left - right;
    const plotH = height - top - bottom;
    const lows = candles.map(c => Number(c.l));
    const highs = candles.map(c => Number(c.h));
    let min = Math.min(...lows), max = Math.max(...highs);
    const spread = Math.max(max - min, max * .001);
    min -= spread * .08; max += spread * .08;
    const y = (v) => top + (max - v) / (max - min) * plotH;
    const x = (i) => left + (i + .5) * plotW / candles.length;
    const slot = plotW / candles.length;
    const bodyW = Math.max(2, Math.min(9, slot * .62));

    ctx.font = '9px ui-monospace, monospace';
    ctx.textAlign = 'right';
    for (let j = 0; j <= 4; j++) {
      const yy = top + (plotH * j / 4);
      const val = max - (max - min) * j / 4;
      ctx.strokeStyle = 'rgba(133,157,185,.13)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(width - right + 3, yy); ctx.stroke();
      ctx.fillStyle = '#71839a';
      ctx.fillText(val.toFixed(4), width - 5, yy + 3);
    }

    const a = s.analysis || {};
    if (a.support_31 && a.support_31 >= min && a.support_31 <= max) {
      ctx.setLineDash([3, 5]); ctx.strokeStyle = 'rgba(69,212,154,.45)';
      ctx.beginPath(); ctx.moveTo(left, y(a.support_31)); ctx.lineTo(width - right + 2, y(a.support_31)); ctx.stroke(); ctx.setLineDash([]);
    }
    if (a.resistance_31 && a.resistance_31 >= min && a.resistance_31 <= max) {
      ctx.setLineDash([3, 5]); ctx.strokeStyle = 'rgba(255,112,133,.42)';
      ctx.beginPath(); ctx.moveTo(left, y(a.resistance_31)); ctx.lineTo(width - right + 2, y(a.resistance_31)); ctx.stroke(); ctx.setLineDash([]);
    }

    candles.forEach((c, i) => {
      const cx = x(i), openY = y(c.o), closeY = y(c.c), highY = y(c.h), lowY = y(c.l);
      const up = Number(c.c) >= Number(c.o);
      const color = up ? '#45d49a' : '#ff7085';
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx, highY); ctx.lineTo(cx, lowY); ctx.stroke();
      const topBody = Math.min(openY, closeY), bodyH = Math.max(1.5, Math.abs(closeY - openY));
      ctx.fillRect(cx - bodyW / 2, topBody, bodyW, bodyH);
    });

    const times = candles.map(c => Number(c.t));
    const firstTime = times[0], lastTime = times[times.length - 1];
    const pivotList = a.pivots || [];
    pivotList.forEach((p) => {
      const idx = times.indexOf(Number(p.time));
      if (idx < 0) return;
      const px = x(idx), py = y(p.price), lowPivot = p.type === 'LOW';
      ctx.fillStyle = '#e9bd69'; ctx.beginPath(); ctx.arc(px, py, 3.2, 0, Math.PI * 2); ctx.fill();
      ctx.font = '8px ui-monospace, monospace'; ctx.textAlign = 'center'; ctx.fillStyle = '#d5c18e';
      const label = p.nearest_prime ? `P${p.nearest_prime}` : (lowPivot ? 'L' : 'H');
      ctx.fillText(label, px, py + (lowPivot ? 13 : -7));
    });

    (s.signals || []).forEach((sig) => {
      const idx = times.indexOf(Number(sig.bar_time));
      if (idx < 0) return;
      const xx = x(idx), yy = y(sig.entry), isLong = sig.side === 'LONG';
      ctx.fillStyle = isLong ? '#45d49a' : '#ff7085';
      ctx.beginPath();
      ctx.moveTo(xx, yy + (isLong ? 9 : -9));
      ctx.lineTo(xx - 4, yy + (isLong ? 16 : -16));
      ctx.lineTo(xx + 4, yy + (isLong ? 16 : -16));
      ctx.closePath(); ctx.fill();
    });

    const lastCandle = candles[candles.length - 1];
    if (lastCandle) {
      const py = y(lastCandle.c);
      ctx.strokeStyle = 'rgba(77,213,208,.7)'; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right + 2, py); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.textAlign = 'left'; ctx.fillStyle = '#71839a'; ctx.font = '8px ui-monospace, monospace';
    [0, Math.floor(candles.length / 3), Math.floor(candles.length * 2 / 3), candles.length - 1].forEach((idx) => {
      const xx = x(idx); ctx.fillText(new Date(candles[idx].t).toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' }), xx - 16, height - 6);
    });
  }

  async function refresh() {
    try {
      const response = await fetch('/api/state', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const state = await response.json();
      latestState = state;
      setConnection(state.connection, state.error);
      if (!state.ready) {
        if (state.error) $('chartEmpty').textContent = state.error;
        return;
      }
      updateMarket(state);
      updateAI(state);
      updatePortfolio(state);
      renderFastEntry(state.fast_entry || {}, state.cross_market || {}, (state.committee_cycle || {}).fast_entry_gate_mode || 'ADVISORY');
      updateSignals(state);
      updateWaveDiagnostics(state);
      updateLearning(state);
      renderSignals(state.signals || []);
      drawChart(state);
    } catch (error) {
      setConnection('offline', error.message);
    }
  }

  $('notifyBtn').addEventListener('click', async () => {
    if (!('Notification' in window)) {
      showToast('Este navegador no admite notificaciones de escritorio.');
      return;
    }
    const permission = await Notification.requestPermission();
    notifyAllowed = permission === 'granted';
    $('notifyBtn').textContent = notifyAllowed ? '✓ Alertas activadas' : 'Activar alertas';
    showToast(notifyAllowed ? 'Recibirás una notificación cuando aparezca un LONG o SHORT confirmado.' : 'No se concedió permiso para notificaciones.');
  });

  window.addEventListener('resize', () => { if (latestState) drawChart(latestState); });
  refresh();
  setInterval(refresh, 5000);
})();
