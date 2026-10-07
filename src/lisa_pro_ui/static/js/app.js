(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    status: null,
    busy: false,
    phases: [],
    control: null,
    history: [],
    controlDirty: false,
    controlHydrated: false,
    startDirty: false,
    dayDirty: false,
  };

  const MODE_LABELS = {
    day: "Day",
    night: "Night",
    night_silent: "Silent",
  };
  const MODES = ["day", "night", "night_silent"];
  const LED_MODES = ["day", "night"];

  const CHART_SERIES = [
    { key: "fan_command", label: "Fan cmd %", color: "#b8f255", axis: "fan" },
    { key: "fan_actual", label: "Fan actual %", color: "#6dffb0", axis: "fan" },
    { key: "fan_min_limit", label: "Min limit %", color: "rgba(238,247,240,0.35)", axis: "fan", dash: true },
    { key: "fan_max_limit", label: "Max limit %", color: "rgba(255,184,77,0.55)", axis: "fan", dash: true },
    { key: "vpd_inside", label: "VPD in", color: "#7ec8ff", axis: "vpd" },
    { key: "vpd_outside", label: "VPD out", color: "#c4a1ff", axis: "vpd" },
    { key: "vpd_target", label: "VPD target", color: "#ffb84d", axis: "vpd" },
  ];

  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.hidden = true;
    }, 2800);
  }

  function confirmAction(message) {
    return new Promise((resolve) => {
      const dlg = $("confirmDlg");
      $("confirmMsg").textContent = message;
      dlg.showModal();
      dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    });
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { error: text };
    }
    if (!res.ok) {
      throw new Error((data && data.error) || res.statusText || "Request failed");
    }
    return data;
  }

  function fmt1(n) {
    return n == null || Number.isNaN(n) ? "—" : Number(n).toFixed(1);
  }

  function fmt2(n) {
    return n == null || Number.isNaN(n) ? "—" : Number(n).toFixed(2);
  }

  function fmt0(n) {
    return n == null || Number.isNaN(n) ? "—" : String(Math.round(Number(n)));
  }

  function formatUptime(sec) {
    if (sec == null || Number.isNaN(sec)) return "—";
    sec = Math.floor(+sec);
    const d = Math.floor(sec / 86400);
    sec -= d * 86400;
    const h = Math.floor(sec / 3600);
    sec -= h * 3600;
    const m = Math.floor(sec / 60);
    const s = sec - m * 60;
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    return `${m}m ${s}s`;
  }

  function formatClock(ts) {
    const d = new Date(ts * 1000);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
  }

  function setBar(id, pct) {
    const el = $(id);
    if (!el) return;
    const v = Math.max(0, Math.min(100, Number(pct) || 0));
    el.style.width = `${v}%`;
  }

  function setLink(ok, label) {
    $("linkDot").className = `pulse-dot ${ok ? "ok" : "err"}`;
    $("linkLabel").textContent = label;
  }

  function calcVpd(tempC, rh) {
    if (tempC == null || rh == null || Number.isNaN(+tempC) || Number.isNaN(+rh)) return null;
    const t = +tempC;
    const h = Math.max(0, Math.min(100, +rh));
    const svp = 0.6108 * Math.exp((17.27 * t) / (t + 237.3));
    return svp * (1 - h / 100);
  }

  function updateHero(s) {
    const growOn = !!(s.grow && s.grow.started);
    const dryOn = !!(s.drying && s.drying.active);
    const phase = (s.grow && s.grow.phase) || (dryOn ? "Trocknung" : "Idle");
    $("phaseTag").textContent = phase;
    if (growOn) {
      $("heroTitle").textContent = `Day ${s.grow.day} of ${s.grow.total_days}`;
      $("heroSub").textContent = `${s.seed || "Crop"} · canopy climate under active control.`;
    } else if (dryOn) {
      $("heroTitle").textContent = `Drying day ${s.drying.day || 0}`;
      $("heroSub").textContent = "Drying mode is active. Grow cycle is idle.";
    } else {
      $("heroTitle").textContent = "Ready to grow";
      $("heroSub").textContent = "No cycle running. Start a grow or drying mode when you’re set.";
    }

    const btnGrow = $("btnGrow");
    const btnDry = $("btnDry");
    btnGrow.textContent = growOn ? "Stop grow" : "Start grow";
    btnGrow.classList.toggle("danger", growOn);
    btnDry.textContent = dryOn ? "Stop drying" : "Start drying";
    btnDry.classList.toggle("danger", dryOn);
  }

  function updateClimate(s, ctrlState) {
    $("tempIn").textContent = fmt1(s.temp_c);
    $("humiIn").textContent = fmt1(s.humi_rh);
    const vpdIn = s.vpd_kpa != null ? s.vpd_kpa : calcVpd(s.temp_c, s.humi_rh);
    const vpdOut =
      (ctrlState && ctrlState.vpd_outside) != null
        ? ctrlState.vpd_outside
        : calcVpd(s.temp_out_c, s.humi_out_rh);
    $("vpd").textContent = fmt2(vpdIn);
    $("vpdOut").textContent = fmt2(vpdOut);
    $("vpdTarget").textContent = fmt2(ctrlState && ctrlState.vpd_target);
    $("tempOut").textContent = fmt1(s.temp_out_c);
    $("ledGlow").classList.toggle("on", !!s.light_on);
  }

  function updateActuators(s) {
    $("lightVal").textContent = `${fmt1(s.light_pct)}% ${s.light_on ? "ON" : "OFF"}`;
    setBar("lightBar", s.light_pct);
    $("fanVal").textContent = `${fmt0(s.fan_pct)}% · ${fmt0(s.fan_rpm)} rpm`;
    setBar("fanBar", s.fan_pct);
    $("fan3Val").textContent = `${fmt0(s.fan3_pct)}%`;
    setBar("fan3Bar", s.fan3_pct);
    $("pumpVal").textContent = s.pump_on ? "ON" : "OFF";
    $("pumpMeter").className = `meter flat${s.pump_on ? " on" : ""}`;
    $("doorVal").textContent = s.door_open ? "OPEN" : "CLOSED";
    $("doorMeter").className = `meter flat${s.door_open ? " warn" : " on"}`;
    const pos = s.stepper && s.stepper.pos_mm;
    const max = s.stepper && s.stepper.max_travel_mm;
    $("panelVal").textContent = pos == null ? "—" : `${fmt1(pos)} mm`;
    setBar("panelBar", max ? (pos / max) * 100 : 0);
  }

  function updateGrowStrip(s) {
    const g = s.grow || {};
    if (g.started) {
      $("growDay").textContent = `#${g.day}`;
      const pct = g.total_days ? Math.min(100, (g.day / g.total_days) * 100) : 0;
      $("growProgress").style.width = `${pct}%`;
      $("growMeta").textContent = `${g.phase || "Grow"} · ${g.total_days} days planned`;
      if (!state.dayDirty) $("dayInput").value = g.day || 1;
    } else if (s.drying && s.drying.active) {
      $("growDay").textContent = `D${s.drying.day || 0}`;
      $("growProgress").style.width = "0%";
      $("growMeta").textContent = "Drying mode active";
    } else {
      $("growDay").textContent = "—";
      $("growProgress").style.width = "0%";
      $("growMeta").textContent = "No active cycle";
    }
    $("ntpTime").textContent = s.ntp_time || "—";
  }

  function updateHealth(s) {
    const grid = $("healthGrid");
    grid.innerHTML = "";
    const modules = (s.health && s.health.modules) || {};
    const names = {
      sht_in: "SHT in",
      sht_out: "SHT out",
      dac: "DAC",
      fan: "Fan",
      rtc: "RTC",
      tof: "ToF",
      i2c0: "I2C0",
      i2c1: "I2C1",
      fs: "FS",
    };
    Object.keys(modules).forEach((key) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `<span class="dot ${modules[key] ? "ok" : ""}"></span>${names[key] || key}`;
      grid.appendChild(chip);
    });
    $("fwVal").textContent = s.fw || "—";
    $("buildVal").textContent = s.build || "—";
    $("uptimeVal").textContent = formatUptime(s.uptime_s);
    $("espTempVal").textContent = s.esp_temp == null ? "—" : `${fmt1(s.esp_temp)} °C`;
    $("wifiVal").textContent = `${s.ssid || "—"} · ${s.rssi != null ? s.rssi + " dBm" : "—"}`;
    if (s.mqtt) {
      $("mqttStateVal").textContent = s.mqtt.connected
        ? "connected"
        : s.mqtt.enabled
          ? "enabled, offline"
          : "disabled";
    }
  }

  function renderStatus(s) {
    state.status = s;
    const cs = (state.control && state.control.state) || {};
    setLink(true, s.internet_ok ? "Online" : s.wifi_connected ? "LAN only" : "Connected");
    updateHero(s);
    updateClimate(s, cs);
    updateActuators(s);
    updateGrowStrip(s);
    updateHealth(s);
    if (!state.startDirty) {
      if (s.seed) $("seedInput").value = s.seed;
      if (s.grow && s.grow.total_days) $("totalDaysInput").value = s.grow.total_days;
    }
  }

  function updateControlLive(st, enabled) {
    $("pidMode").textContent = st.mode || "—";
    $("pidCmd").textContent =
      st.fan_command == null ? "—" : `${fmt0(st.fan_command)}% (${fmt0(st.fan_min_limit)}–${fmt0(st.fan_max_limit)})`;
    $("pidErr").textContent = st.pid_error == null ? "—" : `${Number(st.pid_error).toFixed(3)} kPa`;
    $("pidReason").textContent = st.reason || "—";
    $("pidStatus").classList.toggle("throttling", !!st.throttling);
    $("pidStatus").classList.toggle("active", !!enabled);
    if (state.status) updateClimate(state.status, st);
  }

  function renderControl(snap, { forceForm = false } = {}) {
    state.control = snap;
    const cfg = snap.config || {};
    const st = snap.state || {};
    updateControlLive(st, cfg.enabled);

    if (snap.history) {
      state.history = snap.history;
      drawChart(state.history);
    }

    if (state.controlDirty && !forceForm) return;
    if (state.controlHydrated && !forceForm) return;

    const vo = cfg.vpd_overwrite || {};
    const pid = cfg.pid || {};
    $("pidEnabled").checked = !!cfg.enabled;
    $("vpdOwEnabled").checked = !!vo.enabled;
    setDualRange($("vpdRange"), vo.vpd_min ?? 1, vo.vpd_max ?? 1.2);
    $("pidKp").value = pid.kp ?? "";
    $("pidKi").value = pid.ki ?? "";
    $("pidKd").value = pid.kd ?? "";
    $("pidRamp").value = pid.ramp_pct_per_min ?? "";
    $("pidInterval").value = pid.interval_s ?? "";
    $("pidDeadband").value = pid.deadband_kpa ?? "";
    renderStageCards(state.phases, cfg.fan_limits || {}, cfg.led || {});
    state.controlHydrated = true;
    state.controlDirty = false;
  }

  function dualRangeHtml({ phaseId, mode, min, max, label }) {
    return `<div class="dual-range" data-phase="${phaseId}" data-mode="${mode}" data-min="0" data-max="100" data-step="1" data-unit="%">
      <div class="dual-range-head">
        <span>${label}</span>
        <strong class="mono dual-range-label">${Math.round(min)}–${Math.round(max)}%</strong>
      </div>
      <div class="dual-range-track">
        <div class="dual-range-fill"></div>
        <input class="dual-lo" type="range" min="0" max="100" step="1" value="${min}"
          data-phase="${phaseId}" data-mode="${mode}" data-field="fan_min"
          aria-label="${label} min" />
        <input class="dual-hi" type="range" min="0" max="100" step="1" value="${max}"
          data-phase="${phaseId}" data-mode="${mode}" data-field="fan_max"
          aria-label="${label} max" />
      </div>
    </div>`;
  }

  function ledSliderHtml({ phaseId, mode, value, label }) {
    return `<label class="led-slider">
      <span class="led-slider-head">
        <span>${label}</span>
        <strong class="mono led-val">${Math.round(value)}%</strong>
      </span>
      <input type="range" min="0" max="100" step="1" value="${value}"
        data-led-phase="${phaseId}" data-led-mode="${mode}" aria-label="${label}" />
    </label>`;
  }

  function syncDualRange(root) {
    if (!root) return;
    const lo = root.querySelector(".dual-lo");
    const hi = root.querySelector(".dual-hi");
    const fill = root.querySelector(".dual-range-fill");
    const label = root.querySelector(".dual-range-label");
    if (!lo || !hi) return;
    let a = Number(lo.value);
    let b = Number(hi.value);
    if (a > b) {
      if (document.activeElement === lo) hi.value = a;
      else lo.value = b;
      a = Number(lo.value);
      b = Number(hi.value);
    }
    const min = Number(lo.min);
    const max = Number(lo.max);
    const span = Math.max(1e-9, max - min);
    const left = ((a - min) / span) * 100;
    const right = ((b - min) / span) * 100;
    if (fill) {
      fill.style.left = `${left}%`;
      fill.style.width = `${Math.max(0, right - left)}%`;
    }
    const unit = root.dataset.unit || "%";
    const step = Number(root.dataset.step || lo.step || 1);
    const fmt = (n) => (step < 1 ? Number(n).toFixed(2) : String(Math.round(n)));
    if (label) label.textContent = `${fmt(a)}–${fmt(b)}${unit}`;
    // Keep the thumb being dragged on top
    lo.style.zIndex = a > max - (max - min) * 0.05 ? "5" : "3";
    hi.style.zIndex = b < min + (max - min) * 0.05 ? "5" : "4";
  }

  function setDualRange(root, loVal, hiVal) {
    if (!root) return;
    const lo = root.querySelector(".dual-lo");
    const hi = root.querySelector(".dual-hi");
    if (!lo || !hi) return;
    let a = Number(loVal);
    let b = Number(hiVal);
    if (a > b) [a, b] = [b, a];
    lo.value = a;
    hi.value = b;
    syncDualRange(root);
  }

  function bindDualRanges(scope) {
    (scope || document).querySelectorAll(".dual-range").forEach((root) => {
      if (root.dataset.bound === "1") {
        syncDualRange(root);
        return;
      }
      root.dataset.bound = "1";
      root.querySelectorAll(".dual-lo, .dual-hi").forEach((input) => {
        input.addEventListener("input", () => {
          syncDualRange(root);
          markControlDirty();
        });
      });
      syncDualRange(root);
    });
  }

  function renderStageCards(phases, limits, ledMap) {
    const root = $("fanLimitsRoot");
    root.innerHTML = "";
    (phases || []).forEach((phase) => {
      const id = String(phase.id ?? 0);
      const card = document.createElement("article");
      card.className = "stage-card";
      const rows = MODES.map((mode) => {
        const lim = (limits[id] && limits[id][mode]) || {};
        const device = ((phase.settings || {})[mode]) || {};
        const min = lim.fan_min != null ? lim.fan_min : device.fan_min ?? 20;
        const max = lim.fan_max != null ? lim.fan_max : device.fan_max ?? 80;
        const ledBlock =
          mode === "day" || mode === "night"
            ? ledSliderHtml({
                phaseId: id,
                mode,
                value: (ledMap[id] && ledMap[id][mode] != null)
                  ? ledMap[id][mode]
                  : device.led ?? (mode === "day" ? 40 : 0),
                label: "LED",
              })
            : `<div class="led-slider muted"><span class="led-slider-head"><span>LED</span><strong class="mono">—</strong></span></div>`;
        return `<div class="stage-mode-row">
          <p class="phase-block-title">${MODE_LABELS[mode]}</p>
          <div class="stage-mode-controls">
            ${dualRangeHtml({
              phaseId: id,
              mode,
              min,
              max,
              label: "Fan",
            })}
            ${ledBlock}
          </div>
        </div>`;
      }).join("");
      card.innerHTML = `
        <div class="phase-card-head">
          <h3>${phase.name || `Phase ${id}`}</h3>
          <span class="phase-id mono">id ${id}</span>
        </div>
        ${rows}`;
      root.appendChild(card);
    });
    root.querySelectorAll(".led-slider input[type=range]").forEach((input) => {
      input.addEventListener("input", () => {
        const val = input.parentElement.querySelector(".led-val");
        if (val) val.textContent = `${Math.round(Number(input.value))}%`;
        markControlDirty();
      });
    });
    bindDualRanges(root);
  }

  function collectFanLimits() {
    const out = {};
    document.querySelectorAll("#fanLimitsRoot input[data-field]").forEach((el) => {
      const phaseId = el.dataset.phase;
      const mode = el.dataset.mode;
      const field = el.dataset.field;
      out[phaseId] = out[phaseId] || {};
      out[phaseId][mode] = out[phaseId][mode] || {};
      out[phaseId][mode][field] = Number(el.value);
    });
    return out;
  }

  function collectLed() {
    const out = {};
    document.querySelectorAll("#fanLimitsRoot input[data-led-phase]").forEach((el) => {
      const phaseId = el.dataset.ledPhase;
      const mode = el.dataset.ledMode;
      out[phaseId] = out[phaseId] || {};
      out[phaseId][mode] = Number(el.value);
    });
    return out;
  }

  function collectControlPayload() {
    return {
      enabled: $("pidEnabled").checked,
      vpd_overwrite: {
        enabled: $("vpdOwEnabled").checked,
        vpd_min: Number($("vpdOwMin").value),
        vpd_max: Number($("vpdOwMax").value),
      },
      pid: {
        kp: Number($("pidKp").value),
        ki: Number($("pidKi").value),
        kd: Number($("pidKd").value),
        ramp_pct_per_min: Number($("pidRamp").value),
        interval_s: Number($("pidInterval").value),
        deadband_kpa: Number($("pidDeadband").value),
      },
      fan_limits: collectFanLimits(),
      led: collectLed(),
    };
  }

  function markControlDirty() {
    state.controlDirty = true;
  }

  function drawChart(history) {
    const canvas = $("pidChart");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 960;
    const cssH = 360;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const w = cssW;
    const h = cssH;
    const pad = { t: 18, r: 54, b: 42, l: 46 };
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(8,28,20,0.35)";
    ctx.fillRect(0, 0, w, h);

    const legend = $("chartLegend");
    if (legend) {
      legend.innerHTML = CHART_SERIES.map(
        (s) => `<span class="legend-item"><i style="background:${s.color}"></i>${s.label}</span>`
      ).join("");
    }

    if (!history || history.length < 2) {
      ctx.fillStyle = "rgba(238,247,240,0.45)";
      ctx.font = "14px Outfit, sans-serif";
      ctx.fillText("Waiting for PID samples…", pad.l, h / 2);
      return;
    }

    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    const t0 = history[0].t;
    const t1 = history[history.length - 1].t || t0 + 1;
    const span = Math.max(1, t1 - t0);

    const xOf = (t) => pad.l + ((t - t0) / span) * plotW;
    const yFan = (v) => pad.t + (1 - Math.max(0, Math.min(100, v)) / 100) * plotH;
    const yVpd = (v) => pad.t + (1 - Math.max(0, Math.min(2.5, v)) / 2.5) * plotH;

    // Horizontal grid + left/right axis labels
    ctx.strokeStyle = "rgba(238,247,240,0.08)";
    ctx.lineWidth = 1;
    ctx.fillStyle = "rgba(238,247,240,0.4)";
    ctx.font = "11px JetBrains Mono, monospace";
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (plotH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + plotW, y);
      ctx.stroke();
      const fanLabel = String(100 - i * 25);
      const vpdLabel = (2.5 - i * 0.625).toFixed(2);
      ctx.fillText(fanLabel, 8, y + 4);
      ctx.fillText(vpdLabel, w - 46, y + 4);
    }

    // Timeline (vertical ticks + clock labels)
    const tickCount = Math.min(6, Math.max(2, Math.floor(plotW / 120)));
    ctx.strokeStyle = "rgba(238,247,240,0.12)";
    ctx.fillStyle = "rgba(238,247,240,0.55)";
    for (let i = 0; i <= tickCount; i++) {
      const t = t0 + (span * i) / tickCount;
      const x = xOf(t);
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, pad.t + plotH);
      ctx.stroke();
      const label = formatClock(t);
      const tw = ctx.measureText(label).width;
      ctx.fillText(label, Math.min(w - pad.r - tw, Math.max(pad.l, x - tw / 2)), h - 14);
    }
    ctx.fillStyle = "rgba(238,247,240,0.35)";
    ctx.fillText("time →", pad.l, h - 14);

    CHART_SERIES.forEach((series) => {
      ctx.beginPath();
      ctx.strokeStyle = series.color;
      ctx.lineWidth = series.dash ? 1.25 : 2;
      ctx.setLineDash(series.dash ? [5, 4] : []);
      let started = false;
      history.forEach((pt) => {
        const raw = pt[series.key];
        if (raw == null || Number.isNaN(+raw)) {
          started = false;
          return;
        }
        const x = xOf(pt.t);
        const y = series.axis === "fan" ? yFan(+raw) : yVpd(+raw);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();
    });
    ctx.setLineDash([]);
  }

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((btn) => {
      const on = btn.dataset.tab === name;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      const on = panel.id === `tab-${name}`;
      panel.classList.toggle("active", on);
      panel.hidden = !on;
    });
    if (name === "chart") drawChart(state.history);
  }

  async function refreshStatus() {
    try {
      const s = await api("/api/proxy/status");
      renderStatus(s);
    } catch (err) {
      setLink(false, "Unreachable");
      console.error(err);
    }
  }

  async function refreshControl({ forceForm = false } = {}) {
    try {
      const snap = await api("/api/control");
      renderControl(snap, { forceForm });
    } catch (err) {
      console.error(err);
    }
  }

  function hhmm(h, m) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }

  async function loadSettings() {
    try {
      const [info, silent, door, mqtt, phases] = await Promise.all([
        api("/api/proxy/info"),
        api("/api/proxy/silent"),
        api("/api/proxy/door-actions"),
        api("/api/proxy/mqtt"),
        api("/api/proxy/phases"),
      ]);

      state.phases = phases.phases || [];

      if (info.notify) {
        $("notifyEnabled").checked = !!info.notify.enabled;
        $("notifyPhone").value = info.notify.phone || "";
      }
      if (info.seed) $("seedInput").value = info.seed;

      $("silentEnabled").checked = !!silent.enabled;
      $("silentStart").value = hhmm(silent.startH, silent.startM);
      $("silentEnd").value = hhmm(silent.endH, silent.endM);
      $("silentFExhMin").value = silent.fanExhaustMin;
      $("silentFExhMax").value = silent.fanExhaustMax;
      $("silentFCircMin").value = silent.fanCircMin;
      $("silentFCircMax").value = silent.fanCircMax;
      $("silentPump").checked = !!silent.pumpEnabled;

      $("doorPause").checked = !!door.pauseControl;
      $("doorLight").checked = !!door.lightOn;
      $("doorPump").checked = !!door.pumpOff;
      $("doorLift").checked = !!door.liftPanel;

      $("mqttEnabled").checked = !!mqtt.enabled;
      $("mqttServer").value = mqtt.server || "";
      $("mqttPort").value = mqtt.port || 1883;
      $("mqttUser").value = mqtt.user || "";
      $("mqttPass").value = mqtt.pass || "";

      await refreshControl({ forceForm: true });
    } catch (err) {
      console.error(err);
    }
  }

  async function withBusy(fn) {
    if (state.busy) return;
    state.busy = true;
    try {
      await fn();
    } finally {
      state.busy = false;
    }
  }

  $("btnGrow").addEventListener("click", () =>
    withBusy(async () => {
      const started = !!(state.status && state.status.grow && state.status.grow.started);
      if (started) {
        if (!(await confirmAction("Stop grow and reset the cycle?"))) return;
        await api("/api/proxy/grow", { method: "POST", body: JSON.stringify({ action: "stop" }) });
        toast("Grow stopped");
      } else {
        await api("/api/proxy/grow", {
          method: "POST",
          body: JSON.stringify({
            action: "start",
            total_days: Number($("totalDaysInput").value || 90),
            start_day: Number($("startDayInput").value || 1),
            seed: $("seedInput").value.trim() || undefined,
          }),
        });
        toast("Grow started");
      }
      await refreshStatus();
    })
  );

  $("btnDry").addEventListener("click", () =>
    withBusy(async () => {
      const active = !!(state.status && state.status.drying && state.status.drying.active);
      if (active && !(await confirmAction("Stop drying mode?"))) return;
      await api("/api/proxy/drying", {
        method: "POST",
        body: JSON.stringify({ action: active ? "stop" : "start" }),
      });
      toast(active ? "Drying stopped" : "Drying started");
      await refreshStatus();
    })
  );

  $("dayForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    state.dayDirty = false;
    withBusy(async () => {
      const day = Number($("dayInput").value);
      if (!day || day < 1) return;
      await api("/api/proxy/grow", {
        method: "POST",
        body: JSON.stringify({ action: "set_day", day }),
      });
      toast(`Day set to #${day}`);
      await refreshStatus();
    });
  });

  $("silentForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    withBusy(async () => {
      const [sh, sm] = $("silentStart").value.split(":").map(Number);
      const [eh, em] = $("silentEnd").value.split(":").map(Number);
      await api("/api/proxy/silent", {
        method: "POST",
        body: JSON.stringify({
          enabled: $("silentEnabled").checked,
          startH: sh || 0,
          startM: sm || 0,
          endH: eh || 0,
          endM: em || 0,
          fanExhaustMin: Number($("silentFExhMin").value),
          fanExhaustMax: Number($("silentFExhMax").value),
          fanCircMin: Number($("silentFCircMin").value),
          fanCircMax: Number($("silentFCircMax").value),
          pumpEnabled: $("silentPump").checked,
        }),
      });
      toast("Silent settings saved");
    });
  });

  $("doorForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    withBusy(async () => {
      await api("/api/proxy/door-actions", {
        method: "POST",
        body: JSON.stringify({
          pauseControl: $("doorPause").checked,
          lightOn: $("doorLight").checked,
          pumpOff: $("doorPump").checked,
          liftPanel: $("doorLift").checked,
        }),
      });
      toast("Door actions saved");
    });
  });

  $("mqttForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    withBusy(async () => {
      await api("/api/proxy/mqtt", {
        method: "POST",
        body: JSON.stringify({
          enabled: $("mqttEnabled").checked,
          server: $("mqttServer").value.trim(),
          port: Number($("mqttPort").value || 1883),
          user: $("mqttUser").value,
          pass: $("mqttPass").value,
        }),
      });
      toast("MQTT saved");
    });
  });

  $("notifyForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    withBusy(async () => {
      const key = $("notifyKey").value.trim();
      const payload = {
        enabled: $("notifyEnabled").checked,
        phone: $("notifyPhone").value.trim(),
      };
      if (key && key !== "***" && key !== "*******") payload.apikey = key;
      await api("/api/proxy/notify", { method: "POST", body: JSON.stringify(payload) });
      toast("Notify saved");
      $("notifyKey").value = "";
    });
  });

  $("btnControlSave").addEventListener("click", () =>
    withBusy(async () => {
      const payload = collectControlPayload();
      if (payload.vpd_overwrite.vpd_min > payload.vpd_overwrite.vpd_max) {
        toast("VPD min > max");
        return;
      }
      for (const [phaseId, modes] of Object.entries(payload.fan_limits)) {
        for (const [mode, lim] of Object.entries(modes)) {
          if (lim.fan_min > lim.fan_max) {
            toast(`Phase ${phaseId} ${mode}: min > max`);
            return;
          }
        }
      }
      const res = await api("/api/control", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.controlDirty = false;
      state.controlHydrated = false;
      renderControl({ config: res.config, state: res.state, history: state.history }, { forceForm: true });
      toast("Control settings saved");
    })
  );

  $("btnSeedFans").addEventListener("click", () =>
    withBusy(async () => {
      if (state.controlDirty && !(await confirmAction("Discard unsaved control edits and re-seed from device?"))) {
        return;
      }
      await api("/api/control/seed-fans", { method: "POST", body: "{}" });
      state.controlDirty = false;
      state.controlHydrated = false;
      await refreshControl({ forceForm: true });
      toast("Fan limits seeded from device");
    })
  );

  $("controlForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    $("btnControlSave").click();
  });

  $("controlForm").addEventListener("input", markControlDirty);
  $("controlForm").addEventListener("change", markControlDirty);
  $("pidEnabled").addEventListener("change", markControlDirty);
  bindDualRanges(document);

  $("startForm").addEventListener("input", () => {
    state.startDirty = true;
  });
  $("dayInput").addEventListener("input", () => {
    state.dayDirty = true;
  });

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  window.addEventListener("resize", () => drawChart(state.history));

  (async function boot() {
    try {
      const meta = await api("/api/meta");
      if (meta.device_url) $("deviceUrl").textContent = meta.device_url;
    } catch (_) {}
    await refreshStatus();
    await loadSettings();
    setInterval(refreshStatus, 2500);
    setInterval(() => refreshControl(), 5000);
  })();
})();
