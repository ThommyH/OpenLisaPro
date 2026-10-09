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

  function calcDewPoint(tempC, rh) {
    if (tempC == null || rh == null || !Number.isFinite(+tempC) || !Number.isFinite(+rh)) return null;
    const temp = Number(tempC);
    const humidity = Math.max(0, Math.min(100, Number(rh)));
    if (humidity === 0) return null;
    const alpha = Math.log(humidity / 100) + (17.27 * temp) / (237.7 + temp);
    return (237.7 * alpha) / (17.27 - alpha);
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

  function currentStagePhase() {
    const grow = state.status && state.status.grow;
    const currentStage = grow && grow.started ? grow.phase : null;
    const normalized = (value) => String(value ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
    const phaseText = normalized(currentStage);

    if (phaseText) {
      return state.phases.find((item) => {
        const id = String(item.id ?? 0);
        const name = normalized(item.name);
        if (phaseText === id || (name && (phaseText === name || phaseText.includes(name) || name.includes(phaseText)))) {
          return true;
        }
        const aliases = {
          flower: ["flower", "blute", "bloom"],
          vegetative: ["vegetative", "veg", "wachstum"],
          seedling: ["seedling", "keim", "clone", "propagat"],
        };
        const key = Object.keys(aliases).find((group) =>
          aliases[group].some((alias) => phaseText.includes(alias)),
        );
        return !!key && aliases[key].some((alias) => name.includes(alias));
      }) || null;
    }
    return null;
  }

  function updateDayTheme(phase) {
    const schedule = (phase && phase.schedule) || {};
    const parseMinutes = (value) => {
      const match = typeof value === "string" && value.match(/^([01]\d|2[0-3]):([0-5]\d)$/);
      return match ? Number(match[1]) * 60 + Number(match[2]) : null;
    };
    const start = parseMinutes(schedule.on);
    const end = parseMinutes(schedule.off);
    let isDay;

    if (start != null && end != null) {
      const timeMatch = String((state.status && state.status.ntp_time) || "").match(/(\d{2}):(\d{2})(?::\d{2})?$/);
      const browserTime = new Date();
      const now = timeMatch ? Number(timeMatch[1]) * 60 + Number(timeMatch[2]) : browserTime.getHours() * 60 + browserTime.getMinutes();
      isDay = start === end || (start < end ? now >= start && now < end : now >= start || now < end);
    } else {
      isDay = !!(state.status && state.status.light_on);
    }
    document.body.classList.toggle("day-mode", isDay);
  }

  function updateCurrentStageCard() {
    const phase = currentStagePhase();
    const currentId = phase ? String(phase.id ?? 0) : null;

    document.querySelectorAll("#fanLimitsRoot .stage-card").forEach((card) => {
      const isCurrent = card.dataset.phaseId === currentId;
      card.classList.toggle("is-current", isCurrent);
      const badge = card.querySelector(".current-stage-badge");
      if (isCurrent && !badge) {
        const currentBadge = document.createElement("span");
        currentBadge.className = "current-stage-badge";
        currentBadge.textContent = "Current stage";
        card.querySelector(".phase-card-head").appendChild(currentBadge);
      } else if (!isCurrent && badge) {
        badge.remove();
      }
    });
    updateDayTheme(phase);
  }

  function updateClimate(s, ctrlState) {
    $("tempIn").textContent = s.temp_c == null ? "—" : `${fmt0(s.temp_c)}°C`;
    $("humiIn").textContent = s.humi_rh == null ? "—" : `${fmt0(s.humi_rh)}%`;
    const dewIn = s.dew_c != null ? s.dew_c : calcDewPoint(s.temp_c, s.humi_rh);
    const dewOut = s.dew_out_c != null ? s.dew_out_c : calcDewPoint(s.temp_out_c, s.humi_out_rh);
    $("dewIn").textContent = dewIn == null ? "—" : `${fmt1(dewIn)}°C`;
    $("dewOut").textContent = dewOut == null ? "—" : `${fmt1(dewOut)}°C`;
    const vpdIn = s.vpd_kpa != null ? s.vpd_kpa : calcVpd(s.temp_c, s.humi_rh);
    const vpdOut =
      (ctrlState && ctrlState.vpd_outside) != null
        ? ctrlState.vpd_outside
        : calcVpd(s.temp_out_c, s.humi_out_rh);
    $("vpd").textContent = vpdIn == null ? "—" : `${fmt2(vpdIn)} kPa`;
    $("vpdOut").textContent = vpdOut == null ? "—" : `${fmt2(vpdOut)} kPa`;
    const vpdTarget = ctrlState && ctrlState.vpd_target;
    const vpdTargetRaw = ctrlState && ctrlState.vpd_target_raw;
    const targetLimited =
      vpdTarget != null && vpdTargetRaw != null && Number(vpdTarget) < Number(vpdTargetRaw) - 0.001;
    $("vpdTarget").textContent = vpdTarget == null
      ? "—"
      : targetLimited
        ? `${fmt2(vpdTargetRaw)} → ${fmt2(vpdTarget)} kPa`
        : `${fmt2(vpdTarget)} kPa`;
    $("vpdTarget").title = targetLimited
      ? "Requested target → achievable target estimated from outside dew point at inside temperature"
      : "Requested VPD target";
    $("tempOut").textContent = s.temp_out_c == null ? "—" : `${fmt0(s.temp_out_c)}°C`;
    $("humiOut").textContent = s.humi_out_rh == null ? "—" : `${fmt0(s.humi_out_rh)}%`;
    $("ledGlow").classList.toggle("on", !!s.light_on);
  }

  function updateActuators(s) {
    $("lightVal").textContent = `${fmt1(s.light_pct)}% ${s.light_on ? "ON" : "OFF"}`;
    setBar("lightBar", s.light_pct);
    document.body.classList.toggle(
      "lights-on",
      !!s.light_on && (s.light_pct ?? 0) > 0,
    );
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
    updateCurrentStageCard();
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
    const fanBand = `(${fmt0(st.fan_min_limit)}–${fmt0(st.fan_max_limit)})`;
    if (st.reason === "ramping" && st.fan_command != null && st.fan_ramp_target != null) {
      $("pidCmd").textContent = `${fmt0(st.fan_command)}% → ${fmt0(st.fan_ramp_target)}% target ${fanBand}`;
    } else {
      $("pidCmd").textContent =
        st.fan_command == null ? "—" : `${fmt0(st.fan_command)}% ${fanBand}`;
    }
    const error = Number(st.pid_error);
    $("pidErr").textContent = st.pid_error == null ? "—" : `${error > 0 ? "+" : ""}${error.toFixed(3)} kPa`;
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

    const pid = cfg.pid || {};
    $("pidEnabled").checked = !!cfg.enabled;
    $("pidKp").value = pid.kp ?? "";
    $("pidKi").value = pid.ki ?? "";
    $("pidKd").value = pid.kd ?? "";
    $("pidRamp").value = pid.ramp_pct_per_min ?? "";
    $("pidInterval").value = pid.interval_s ?? "";
    $("pidDeadband").value = pid.deadband_kpa ?? "";
    renderStageCards(state.phases, cfg.fan_limits || {}, cfg.led || {}, cfg.vpd_targets || {});
    state.controlHydrated = true;
    state.controlDirty = false;
  }

  function dualRangeHtml({
    phaseId,
    mode,
    min,
    max,
    label,
    fieldLo,
    fieldHi,
    rangeMin = 0,
    rangeMax = 100,
    step = 1,
    unit = "%",
    kind = "fan",
  }) {
    const fmt = (n) => (step < 1 ? Number(n).toFixed(2) : String(Math.round(n)));
    return `<div class="dual-range" data-kind="${kind}" data-phase="${phaseId}" data-mode="${mode}"
      data-min="${rangeMin}" data-max="${rangeMax}" data-step="${step}" data-unit="${unit}">
      <div class="dual-range-head">
        <span>${label}</span>
        <strong class="mono dual-range-label">${fmt(min)}–${fmt(max)}${unit}</strong>
      </div>
      <div class="dual-range-track">
        <div class="dual-range-fill"></div>
        <input class="dual-lo" type="range" min="${rangeMin}" max="${rangeMax}" step="${step}" value="${min}"
          data-phase="${phaseId}" data-mode="${mode}" data-field="${fieldLo}" data-kind="${kind}"
          aria-label="${label} min" />
        <input class="dual-hi" type="range" min="${rangeMin}" max="${rangeMax}" step="${step}" value="${max}"
          data-phase="${phaseId}" data-mode="${mode}" data-field="${fieldHi}" data-kind="${kind}"
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

  function renderStageCards(phases, limits, ledMap, vpdMap) {
    const root = $("fanLimitsRoot");
    root.innerHTML = "";
    (phases || []).forEach((phase) => {
      const id = String(phase.id ?? 0);
      const card = document.createElement("article");
      card.className = "stage-card";
      card.dataset.phaseId = id;
      const schedule = phase.schedule || {};
      const scheduleTime = (value, fallback) =>
        typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? value : fallback;
      const rows = MODES.map((mode) => {
        const lim = (limits[id] && limits[id][mode]) || {};
        const vpd = (vpdMap[id] && vpdMap[id][mode]) || {};
        const device = ((phase.settings || {})[mode]) || {};
        const fanMin = lim.fan_min != null ? lim.fan_min : device.fan_min ?? 20;
        const fanMax = lim.fan_max != null ? lim.fan_max : device.fan_max ?? 80;
        const vpdMin = vpd.vpd_min != null ? vpd.vpd_min : device.vpd_min ?? 0.8;
        const vpdMax = vpd.vpd_max != null ? vpd.vpd_max : device.vpd_max ?? 1.2;
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
              min: vpdMin,
              max: vpdMax,
              label: "VPD",
              fieldLo: "vpd_min",
              fieldHi: "vpd_max",
              rangeMin: 0,
              rangeMax: 2.5,
              step: 0.01,
              unit: " kPa",
              kind: "vpd",
            })}
            ${dualRangeHtml({
              phaseId: id,
              mode,
              min: fanMin,
              max: fanMax,
              label: "Fan",
              fieldLo: "fan_min",
              fieldHi: "fan_max",
              rangeMin: 0,
              rangeMax: 100,
              step: 1,
              unit: "%",
              kind: "fan",
            })}
            ${ledBlock}
          </div>
        </div>`;
      }).join("");
      card.innerHTML = `
        <div class="phase-card-head">
          <h3>${phase.name || `Phase ${id}`}</h3>
        </div>
        <div class="phase-schedule">
          <label>Lights on
            <input type="time" value="${scheduleTime(schedule.on, "06:00")}" data-schedule-phase="${id}" data-schedule-field="on" />
          </label>
          <label>Lights off
            <input type="time" value="${scheduleTime(schedule.off, "00:00")}" data-schedule-phase="${id}" data-schedule-field="off" />
          </label>
          <p>Day runs from lights on to lights off, including overnight schedules.</p>
        </div>
        ${rows}`;
      root.appendChild(card);
    });
    updateCurrentStageCard();
    root.querySelectorAll(".led-slider input[type=range]").forEach((input) => {
      input.addEventListener("input", () => {
        const val = input.parentElement.querySelector(".led-val");
        if (val) val.textContent = `${Math.round(Number(input.value))}%`;
        markControlDirty();
      });
    });
    bindDualRanges(root);
  }

  function collectKindLimits(kind, fields) {
    const out = {};
    document.querySelectorAll(`#fanLimitsRoot input[data-kind="${kind}"]`).forEach((el) => {
      const phaseId = el.dataset.phase;
      const mode = el.dataset.mode;
      const field = el.dataset.field;
      if (!fields.includes(field)) return;
      out[phaseId] = out[phaseId] || {};
      out[phaseId][mode] = out[phaseId][mode] || {};
      out[phaseId][mode][field] = Number(el.value);
    });
    return out;
  }

  function collectFanLimits() {
    return collectKindLimits("fan", ["fan_min", "fan_max"]);
  }

  function collectVpdTargets() {
    return collectKindLimits("vpd", ["vpd_min", "vpd_max"]);
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

  function collectPhaseSchedules() {
    const out = {};
    document.querySelectorAll("#fanLimitsRoot input[data-schedule-phase]").forEach((el) => {
      const phaseId = el.dataset.schedulePhase;
      const field = el.dataset.scheduleField;
      out[phaseId] = out[phaseId] || {};
      out[phaseId][field] = el.value;
    });
    return out;
  }

  function collectControlPayload() {
    return {
      enabled: $("pidEnabled").checked,
      vpd_targets: collectVpdTargets(),
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
      phase_schedules: collectPhaseSchedules(),
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
      for (const [phaseId, schedule] of Object.entries(payload.phase_schedules)) {
        if (!schedule.on || !schedule.off) {
          toast(`Phase ${phaseId}: set both lights-on and lights-off times`);
          return;
        }
      }
      for (const [phaseId, modes] of Object.entries(payload.vpd_targets)) {
        for (const [mode, lim] of Object.entries(modes)) {
          if (lim.vpd_min > lim.vpd_max) {
            toast(`Phase ${phaseId} ${mode}: VPD min > max`);
            return;
          }
        }
      }
      for (const [phaseId, modes] of Object.entries(payload.fan_limits)) {
        for (const [mode, lim] of Object.entries(modes)) {
          if (lim.fan_min > lim.fan_max) {
            toast(`Phase ${phaseId} ${mode}: fan min > max`);
            return;
          }
        }
      }
      const res = await api("/api/control", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      Object.entries(payload.phase_schedules).forEach(([phaseId, schedule]) => {
        const phase = state.phases.find((item) => String(item.id ?? 0) === phaseId);
        if (phase) phase.schedule = { ...(phase.schedule || {}), ...schedule };
      });
      state.controlDirty = false;
      state.controlHydrated = false;
      renderControl({ config: res.config, state: res.state, history: state.history }, { forceForm: true });
      toast(res.schedule_error ? `Settings saved; schedule update failed: ${res.schedule_error}` : "Control settings saved");
    })
  );

  $("btnControlReset").addEventListener("click", () =>
    withBusy(async () => {
      const confirmed = await confirmAction(
        "Reset all control settings? This disables the local PID, clears its overrides, and restores the growbox factory phase presets.",
      );
      if (!confirmed) return;
      const res = await api("/api/control/reset", {
        method: "POST",
        body: "{}",
      });
      state.phases = res.phases || [];
      state.controlDirty = false;
      state.controlHydrated = false;
      renderControl(
        { config: res.config, state: res.state, history: state.history },
        { forceForm: true },
      );
      toast("Control settings restored to defaults");
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
