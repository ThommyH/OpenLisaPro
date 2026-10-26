(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    status: null,
    busy: false,
    phases: [],
    control: null,
    history: [],
    chartHours: 48,
    chartOffsetHours: 0,
    chartVisible: { fan_command: true, fan_actual: true, fan_min_limit: false, fan_max_limit: false, vpd_inside: true, vpd_outside: false, vpd_target: true },
    chartLoaded: false,
    controlDirty: false,
    controlHydrated: false,
    haReadOnly: false,
    startDirty: false,
    dayDirty: false,
    silentEnabled: false,
    flowEvents: [],
    flowPage: 1,
    flowTotal: 0,
    flowTotalPages: 1,
    lastFlowSignature: null,
    lastTemperatureAdjustmentPct: null,
    lastScheduledIsDay: null,
  };

  const MODE_LABELS = {
    day: "Day",
    day_silent: "Day silent",
    night: "Night",
    night_silent: "Night silent",
  };

  const PID_REASON_LABELS = {
    target_limited_by_outside_dew_point: "Outside-air limited",
    throttling_unreachable: "Backing off · target unreachable",
    no_inside_vpd: "Missing inside VPD",
    over_temperature: "High temperature · fan max",
    temperature_limited_by_outside: "High temperature · outside too warm",
  };
  const MODES = ["day", "day_silent", "night", "night_silent"];
  const LED_MODES = ["day", "day_silent", "night", "night_silent"];

  const CHART_SERIES = [
    { key: "fan_command", label: window.t("Fan command"), color: "#b8f255", axis: "fan" },
    { key: "fan_actual", label: window.t("Fan actual"), color: "#6dffb0", axis: "fan" },
    { key: "fan_min_limit", label: window.t("Fan min"), color: "rgba(238,247,240,0.55)", axis: "fan", dash: true },
    { key: "fan_max_limit", label: window.t("Fan max"), color: "#ffb84d", axis: "fan", dash: true },
    { key: "vpd_inside", label: window.t("Inside VPD"), color: "#7ec8ff", axis: "vpd" },
    { key: "vpd_outside", label: window.t("Outside VPD"), color: "#c4a1ff", axis: "vpd" },
    { key: "vpd_target", label: window.t("VPD target"), color: "#ffb84d", axis: "vpd", dash: true },
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

  async function checkBuildVersion(force = false) {
    const status = $("buildStatus");
    const label = $("buildLabel");
    const detail = $("buildDetail");
    const button = $("checkBuild");
    if (!status || !label || !detail) return;
    if (button) button.disabled = true;
    status.dataset.state = "checking";
    label.textContent = "Checking releases…";
    try {
      const result = await api(`/api/version${force ? "?refresh=1" : ""}`);
      const current = String(result.version || "development");
      const latest = String(result.latest_version || "");
      label.href = result.latest_url || "https://github.com/ThommyH/OpenLisaPro/tags";
      if (!latest) {
        status.dataset.state = "error";
        label.textContent = "Release check unavailable";
        detail.textContent = `Release ${current} · try again later`;
      } else if (result.is_latest === true) {
        status.dataset.state = "current";
        label.textContent = "Up to date";
        detail.textContent = `Release ${current}`;
      } else if (current === "development") {
        status.dataset.state = "error";
        label.textContent = "Development build";
        detail.textContent = `Latest release ${latest}`;
      } else {
        status.dataset.state = "outdated";
        label.textContent = "Update available";
        detail.textContent = `Running ${current} · latest ${latest}`;
      }
      status.title = result.checked_at ? `${window.t("Checked")} ${new Date(result.checked_at * 1000).toLocaleString(window.i18n.locale)}` : "";
    } catch (error) {
      status.dataset.state = "error";
      label.textContent = "Version check failed";
      detail.textContent = error.message || "Unable to contact the local version endpoint";
    } finally {
      if (button) button.disabled = false;
    }
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

  function fmtSensor(n) {
    const value = Number(n);
    return n == null || !Number.isFinite(value) ? "—" : Number.isInteger(value) ? String(value) : value.toFixed(1);
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
    const activeStage = currentStagePhase();
    const isDay = isDayForPhase(activeStage);
    $("phaseTag").textContent = phase;
    $("dayPeriod").textContent = window.t(isDay ? "DAY" : "NIGHT");
    $("dayPeriodField").dataset.period = isDay ? "day" : "night";
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

  function isDayForPhase(phase) {
    const mode = state.control?.state?.mode;
    if (["day", "day_silent", "night", "night_silent"].includes(mode)) {
      return mode === "day" || mode === "day_silent";
    }

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
      state.lastScheduledIsDay = isDay;
    } else if (state.status?.grow?.started && state.lastScheduledIsDay != null) {
      // A 0% LED command can briefly make the device report its light as off.
      // Keep the schedule-derived period while active-stage data is temporarily missing.
      isDay = state.lastScheduledIsDay;
    } else {
      isDay = !!(state.status && state.status.light_on);
    }
    return isDay;
  }

  function updateDayTheme(phase) {
    document.body.classList.toggle("day-mode", isDayForPhase(phase));
  }

  function updateDayPeriod() {
    const isDay = isDayForPhase(currentStagePhase());
    $("dayPeriod").textContent = window.t(isDay ? "DAY" : "NIGHT");
    $("dayPeriodField").dataset.period = isDay ? "day" : "night";
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
    $("tempIn").textContent = s.temp_c == null ? "—" : `${fmtSensor(s.temp_c)}°C`;
    $("humiIn").textContent = s.humi_rh == null ? "—" : `${fmtSensor(s.humi_rh)}%`;
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
    $("tempOut").textContent = s.temp_out_c == null ? "—" : `${fmtSensor(s.temp_out_c)}°C`;
    $("humiOut").textContent = s.humi_out_rh == null ? "—" : `${fmtSensor(s.humi_out_rh)}%`;
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

  function updateControlLive(st, enabled, manualMode = !!state.control?.config?.manual_mode) {
    updateFanFlow(st, enabled, manualMode);
    $("pidMode").textContent = manualMode ? "Manual" : MODE_LABELS[st.mode] || (st.mode || "—").replace(/_/g, " ");
    const silentButton = $("btnSilentToggle");
    silentButton.textContent = state.silentEnabled ? "Disable growbox silent mode" : "Enable growbox silent mode";
    silentButton.setAttribute("aria-pressed", String(state.silentEnabled));
    silentButton.classList.toggle("primary", state.silentEnabled);
    silentButton.classList.toggle("ghost", !state.silentEnabled);
    const fanBand = `(${fmt0(st.fan_min_limit)}–${fmt0(st.fan_max_limit)})`;
    if (st.reason === "ramping" && st.fan_command != null && st.fan_ramp_target != null) {
      $("pidCmd").textContent = `${fmt0(st.fan_command)}% → ${fmt0(st.fan_ramp_target)}% target ${fanBand}`;
    } else {
      $("pidCmd").textContent =
        st.fan_command == null ? "—" : `${fmt0(st.fan_command)}% ${fanBand}`;
    }
    const error = Number(st.pid_error);
    $("pidErr").textContent = manualMode ? "Ignored" : st.pid_error == null ? "—" : `${error > 0 ? "+" : ""}${error.toFixed(3)} kPa`;
    $("pidReason").textContent = manualMode ? "Direct fan and LED" : PID_REASON_LABELS[st.reason] || (st.reason || "—").replace(/_/g, " ");
    const stateLabel = $("pidReason").parentElement.querySelector(".label");
    stateLabel.textContent = manualMode ? "Control" : st.temperature_warning ? "Temperature warning" : "State";
    if (st.temperature_warning) $("pidReason").textContent = st.temperature_warning;
    $("pidReason").title = st.reason === "target_limited_by_outside_dew_point"
      ? "Outside air is too moist to reach the selected VPD target using ventilation alone."
      : st.reason === "throttling_unreachable"
        ? "The target is outside the estimated ventilation limit, or VPD did not improve after a fan command reached its upper limit. The controller is stepping fan speed down; it may not have reached the ceiling in the outside-air-limited case."
        : st.temperature_warning || (st.temperature_active ? "The controller prioritizes cooling and commands the fan to its configured maximum. LED power may be reduced temporarily." : "");
    $("pidStatus").classList.toggle("throttling", !!st.throttling);
    $("pidStatus").classList.toggle("active", !!enabled || manualMode);
    $("pidEnabled").disabled = manualMode;
    $("pidEnabledLabel").textContent = manualMode ? "PID suspended" : "PID enabled";
    if (state.status) updateClimate(state.status, st);
  }

  function updateFanFlow(st, enabled, manualMode = false) {
    const chart = document.querySelector(".fan-flowchart");
    if (!chart) return;
    const steps = ["resolve-stage"];
    const branches = [];
    const addStep = (key) => steps.push(key);
    const addBranch = (key) => branches.push(key);
    addStep("manual-mode");
    if (manualMode || st.reason === "manual") {
      addBranch("manual-active");
      addStep("write-command");
    } else {
      addBranch("manual-inactive");
      addStep("pid-enabled");
      if (!enabled || st.reason === "disabled") {
        addBranch("pid-disabled");
      } else {
        addBranch("pid-active");
        addStep("measure-vpd");
        if (st.reason !== "no_inside_vpd") {
          addStep("temperature-check");
          if (st.temperature_active) {
            addBranch("temperature-active");
            addStep("write-command");
          } else {
            addBranch("temperature-normal");
            addStep("estimate-limit");
            addStep("target-check");
            addBranch(st.vpd_reachable === false ? "target-limited" : "target-reachable");
            addStep("deadband-check");
            const within = st.pid_error != null && Math.abs(Number(st.pid_error)) <= Number(st.pid_deadband_kpa ?? 0.03);
            addBranch(within ? "within-deadband" : "pid-correction");
            addStep("soft-temperature-check");
            addBranch(st.temperature_soft_triggered ? "soft-temperature-adjust" : "soft-temperature-hold");
            addStep("apply-ramp");
            addStep("write-command");
          }
        }
      }
    }
    const temperatureBoost = Number(st.temperature_adjustment_pct || 0);
    const previousTemperatureBoost = state.lastTemperatureAdjustmentPct;
    const boostChanged = previousTemperatureBoost != null && Math.abs(temperatureBoost - previousTemperatureBoost) >= 0.01;
    state.lastTemperatureAdjustmentPct = temperatureBoost;
    const signature = JSON.stringify([
      steps,
      branches,
      st.reason,
      !!st.throttling,
      Number(temperatureBoost.toFixed(2)),
      manualMode ? Number(st.manual_fan_pct) : null,
      manualMode ? Number(st.manual_led_pct) : null,
    ]);
    const changed = signature !== state.lastFlowSignature;
    if (changed) {
      state.lastFlowSignature = signature;
      const reason = manualMode || st.reason === "manual"
        ? `Manual mode · fan ${fmt0(st.manual_fan_pct)}% · LED ${fmt0(st.manual_led_pct)}%`
        : !enabled || st.reason === "disabled" ? "PID disabled"
        : st.reason === "no_inside_vpd" ? "Waiting for inside VPD"
          : st.temperature_active ? (st.temperature_warning || (st.reason === "temperature_limited_by_outside" ? "High temperature · outside too warm" : "High temperature · fan max"))
            : boostChanged && temperatureBoost < previousTemperatureBoost ? `Soft temperature boost backed off (${fmt1(previousTemperatureBoost)} → ${fmt1(temperatureBoost)} pp)`
              : boostChanged && temperatureBoost > previousTemperatureBoost ? `Soft temperature boost increased (${fmt1(previousTemperatureBoost)} → ${fmt1(temperatureBoost)} pp)`
                : st.throttling ? "Backing off · target unreachable"
                  : st.vpd_reachable === false ? "Outside-air limited"
                    : st.temperature_soft_triggered ? "Soft temperature limit active"
                  : st.reason === "ramping" ? "Ramping toward fan request" : "Tracking VPD target";
      const event = {
        at: Date.now(), title: reason, steps: [...steps], branches: [...branches], signature,
      };
      const fromPct = st.fan_vpd_request == null ? NaN : Number(st.fan_vpd_request);
      const toPct = st.fan_ramp_target == null ? NaN : Number(st.fan_ramp_target);
      if (Number.isFinite(fromPct) && Number.isFinite(toPct)) {
        event.fan_change = {
          from_pct: fromPct,
          to_pct: toPct,
          delta_pct: toPct - fromPct,
          temperature_boost_pct: Number(st.temperature_adjustment_pct || 0),
          command_pct: st.fan_command != null && Number.isFinite(Number(st.fan_command)) ? Number(st.fan_command) : null,
        };
      }
      if (state.flowPage === 1) {
        state.flowEvents.unshift(event);
        state.flowEvents = state.flowEvents.slice(0, 20);
        renderFlowEvents();
      }
      api("/api/control/flow-events", { method: "POST", body: JSON.stringify(event) })
        .then((result) => {
          state.flowTotal = result.total;
          state.flowTotalPages = result.total_pages;
          renderFlowPagination();
        })
        .catch((error) => console.error("Could not persist controller flow change", error));
    }
    applyFlowPath({ steps, branches });
    if (manualMode || st.reason === "manual") {
      $("flowCommandValue").textContent = `Manual command: fan ${fmt0(st.fan_command)}% · LED ${fmt0(st.manual_led_pct)}%`;
      return;
    }
    if (!enabled || st.reason === "disabled") {
      $("flowCommandValue").textContent = "Current command: PID disabled";
      return;
    }
    if (st.reason === "no_inside_vpd") {
      $("flowCommandValue").textContent = "Current command: waiting for inside VPD";
      return;
    }
    const command = st.fan_command == null ? "—" : `${fmt0(st.fan_command)}%`;
    const target = st.fan_ramp_target == null ? null : `${fmt0(st.fan_ramp_target)}% target`;
    const tempCorrection = Number(st.temperature_adjustment_pct || 0);
    const tempNote = st.temperature_soft_triggered
      ? ` · temperature ${tempCorrection > 0 ? "+" : ""}${fmt1(tempCorrection)}%`
      : "";
    $("flowCommandValue").textContent = `Current command: ${command}${st.reason === "ramping" && target ? ` → ${target} (ramping)` : ""}${tempNote}`;
  }

  function applyFlowPath(path) {
    const chart = document.querySelector(".fan-flowchart");
    if (!chart) return;
    chart.querySelectorAll(".active-path").forEach((el) => el.classList.remove("active-path"));
    path.steps.forEach((key) => chart.querySelector(`[data-flow-step="${key}"]`)?.classList.add("active-path"));
    path.branches.forEach((key) => chart.querySelector(`[data-flow-branch="${key}"]`)?.classList.add("active-path"));
  }

  function renderFlowEvents() {
    const list = $("flowEventList");
    if (!list) return;
    list.replaceChildren();
    if (!state.flowEvents.length) {
      const empty = document.createElement("p");
      empty.className = "flow-event-empty";
      empty.textContent = "No controller flow changes yet.";
      list.append(empty);
      return;
    }
    state.flowEvents.forEach((event, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "flow-event";
      button.classList.toggle("selected", index === 0);
      const time = document.createElement("time");
      time.dateTime = new Date(event.at).toISOString();
      time.textContent = new Date(event.at).toLocaleString(window.i18n.locale, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const title = document.createElement("span");
      title.textContent = event.title;
      const copy = document.createElement("span");
      copy.className = "flow-event-copy";
      copy.append(title);
      if (event.fan_change) {
        const change = event.fan_change;
        const detail = document.createElement("small");
        const delta = Number(change.delta_pct);
        const boost = Number(change.temperature_boost_pct || 0);
        const command = Number(change.command_pct);
        const hasSoftBoost = event.title?.startsWith("Soft temperature boost");
        detail.textContent = `Fan target: ${fmt0(change.from_pct)}% → ${fmt0(change.to_pct)}% (${delta > 0 ? "+" : ""}${fmt1(delta)} pp)${boost > 0 || hasSoftBoost ? ` · temperature boost +${fmt1(boost)} pp` : ""}${Number.isFinite(command) ? ` · command ${fmt0(command)}%` : ""}`;
        copy.append(detail);
      }
      button.append(time, copy);
      button.addEventListener("click", () => {
        list.querySelectorAll(".flow-event").forEach((item) => item.classList.toggle("selected", item === button));
        applyFlowPath(event);
      });
      list.append(button);
    });
  }

  function renderFlowPagination() {
    const nav = $("flowEventPagination");
    if (!nav) return;
    nav.hidden = state.flowTotalPages <= 1;
    $("flowEventPageLabel").textContent = `Page ${state.flowPage} of ${state.flowTotalPages} · ${state.flowTotal} changes`;
    $("flowEventsPrevious").disabled = state.flowPage <= 1;
    $("flowEventsNext").disabled = state.flowPage >= state.flowTotalPages;
  }

  async function loadFlowEvents(page = 1) {
    const result = await api(`/api/control/flow-events?page=${page}`);
    state.flowEvents = result.events || [];
    state.flowPage = result.page || 1;
    state.flowTotal = result.total || 0;
    state.flowTotalPages = result.total_pages || 1;
    if (state.flowPage === 1 && state.flowEvents.length && state.lastFlowSignature == null) {
      state.lastFlowSignature = state.flowEvents[0].signature || null;
      const priorBoost = state.flowEvents[0].fan_change?.temperature_boost_pct;
      state.lastTemperatureAdjustmentPct = Number.isFinite(Number(priorBoost)) ? Number(priorBoost) : 0;
    }
    renderFlowEvents();
    renderFlowPagination();
  }

  function renderControl(snap, { forceForm = false } = {}) {
    state.control = snap;
    const cfg = snap.config || {};
    const st = snap.state || {};
    updateControlLive(st, cfg.enabled, cfg.manual_mode);
    updateDayPeriod();

    if (snap.history) {
      mergeHistory(snap.history);
      if (!$('tab-chart').hidden) drawChart(state.history);
    }

    if (state.controlDirty && !forceForm) return;
    if (state.controlHydrated && !forceForm) return;

    const pid = cfg.pid || {};
    $("pidEnabled").checked = !!cfg.enabled;
    $("manualMode").checked = !!cfg.manual_mode;
    $("manualFanPct").value = cfg.manual_fan_pct ?? 40;
    $("manualLedPct").value = cfg.manual_led_pct ?? 50;
    $("manualFanValue").textContent = `${fmt0($("manualFanPct").value)}%`;
    $("manualLedValue").textContent = `${fmt0($("manualLedPct").value)}%`;
    $("manualModeFields").classList.toggle("is-disabled", !cfg.manual_mode);
    $("manualFanPct").disabled = !cfg.manual_mode;
    $("manualLedPct").disabled = !cfg.manual_mode;
    $("pidKp").value = pid.kp ?? "";
    $("pidKi").value = pid.ki ?? "";
    $("pidKd").value = pid.kd ?? "";
    $("pidRamp").value = pid.ramp_pct_per_min ?? "";
    $("pidInterval").value = pid.interval_s ?? "";
    $("pidDeadband").value = pid.deadband_kpa ?? "";
    $("pidTemperatureWeight").value = pid.temperature_weight_pct_per_c ?? 15;
    renderStageCards(state.phases, cfg.fan_limits || {}, cfg.led || {}, cfg.vpd_targets || {}, cfg.temperature_limits || {}, cfg.temperature_soft_limits || {});
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
        <button type="button" class="mono dual-range-label value-edit-trigger" aria-label="Edit ${label} minimum and maximum">${fmt(min)}–${fmt(max)}${unit}</button>
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
    return `<div class="led-slider">
      <span class="led-slider-head">
        <span>${label}</span>
        <button type="button" class="mono led-val value-edit-trigger" aria-label="Edit ${label} level">${Math.round(value)}%</button>
      </span>
      <input type="range" min="0" max="100" step="1" value="${value}"
        data-led-phase="${phaseId}" data-led-mode="${mode}" aria-label="${label}" />
    </div>`;
  }

  function editDualRange(root) {
    if (!root || state.haReadOnly) return;
    const label = root.querySelector(".dual-range-label");
    const lo = root.querySelector(".dual-lo");
    const hi = root.querySelector(".dual-hi");
    if (!label || !lo || !hi || lo.disabled || label.dataset.editing === "1") return;
    const editor = document.createElement("span");
    editor.className = "dual-range-editor";
    const makeInput = (value, name) => {
      const input = document.createElement("input");
      input.type = "number";
      input.required = true;
      input.min = root.dataset.min;
      input.max = root.dataset.max;
      input.step = root.dataset.step;
      input.value = value;
      input.setAttribute("aria-label", `${root.dataset.kind} ${name}`);
      input.title = name === "min" ? "Minimum" : "Maximum";
      return input;
    };
    const minInput = makeInput(lo.value, "min");
    const maxInput = makeInput(hi.value, "max");
    const save = document.createElement("button");
    save.type = "button";
    save.className = "value-edit-save";
    save.textContent = "Done";
    save.setAttribute("aria-label", "Apply minimum and maximum");
    editor.append(minInput, document.createTextNode("–"), maxInput, save);
    label.replaceWith(editor);
    const close = (commit) => {
      if (commit) {
        const minValue = Number(minInput.value);
        const maxValue = Number(maxInput.value);
        if (!minInput.value.trim() || !maxInput.value.trim() || !minInput.checkValidity() || !maxInput.checkValidity() || !Number.isFinite(minValue) || !Number.isFinite(maxValue) || minValue > maxValue) {
          if (minValue > maxValue) maxInput.setCustomValidity("Maximum must be greater than or equal to minimum.");
          (minValue > maxValue ? maxInput : minInput).reportValidity();
          (minValue > maxValue ? maxInput : minInput).focus();
          return;
        }
        maxInput.setCustomValidity("");
        setDualRange(root, minValue, maxValue);
        markControlDirty();
      }
      editor.replaceWith(label);
      syncDualRange(root);
    };
    [minInput, maxInput].forEach((input) => input.addEventListener("input", () => maxInput.setCustomValidity("")));
    save.addEventListener("click", () => close(true));
    [minInput, maxInput].forEach((input) => input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); close(true); }
      if (event.key === "Escape") { event.preventDefault(); close(false); }
    }));
    minInput.focus();
    minInput.select();
  }

  function editLedValue(button) {
    if (!button || state.haReadOnly) return;
    const slider = button.closest(".led-slider")?.querySelector('input[type="range"]');
    if (!slider || slider.disabled || button.dataset.editing === "1") return;
    button.dataset.editing = "1";
    const input = document.createElement("input");
    input.type = "number";
    input.min = slider.min;
    input.max = slider.max;
    input.step = slider.step;
    input.value = slider.value;
    input.className = "led-value-editor mono";
    input.setAttribute("aria-label", "LED percentage");
    button.replaceWith(input);
    let finished = false;
    const finish = (commit) => {
      if (finished) return;
      finished = true;
      if (commit) {
        const value = Number(input.value);
        if (input.value.trim() && Number.isFinite(value)) {
          slider.value = String(Math.max(Number(slider.min), Math.min(Number(slider.max), value)));
          button.textContent = `${Math.round(Number(slider.value))}%`;
          markControlDirty();
        }
      }
      input.replaceWith(button);
      delete button.dataset.editing;
    };
    input.addEventListener("change", () => finish(true), { once: true });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); finish(true); }
      if (event.key === "Escape") { event.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true), { once: true });
    input.focus();
    input.select();
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
      const valueLabel = root.querySelector(".dual-range-label");
      valueLabel?.addEventListener("click", () => editDualRange(root));
      valueLabel?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          editDualRange(root);
        }
      });
      root.querySelectorAll(".dual-lo, .dual-hi").forEach((input) => {
        input.addEventListener("input", () => {
          syncDualRange(root);
          markControlDirty();
        });
      });
      syncDualRange(root);
    });
  }

  function renderStageCards(phases, limits, ledMap, vpdMap, temperatureMap, softTemperatureMap) {
    const root = $("fanLimitsRoot");
    root.innerHTML = "";
    (phases || []).forEach((phase) => {
      const id = String(phase.id ?? 0);
      const card = document.createElement("article");
      card.className = "stage-card";
      card.dataset.phaseId = id;
      const schedule = phase.schedule || {};
      const temperature = temperatureMap[id] || {};
      const softTemperature = softTemperatureMap[id] || {};
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
          mode === "day" || mode === "day_silent"
            ? ledSliderHtml({
                phaseId: id,
                mode,
                value: (ledMap[id] && ledMap[id][mode] != null)
                  ? ledMap[id][mode]
                  : device.led ?? (mode === "day_silent" ? ((phase.settings || {}).day || {}).led ?? 40 : 40),
                label: "LED",
              })
            : mode === "night" || mode === "night_silent"
              ? `<div class="led-slider muted"><span class="led-slider-head"><span>LED</span><strong class="mono">0%</strong></span></div>`
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
          <button type="button" class="btn ghost small" data-reset-stage="${id}">Reset stage</button>
        </div>
        <div class="phase-schedule">
          <label>Lights on
            <input type="time" value="${scheduleTime(schedule.on, "06:00")}" data-schedule-phase="${id}" data-schedule-field="on" />
          </label>
          <label>Lights off
            <input type="time" value="${scheduleTime(schedule.off, "00:00")}" data-schedule-phase="${id}" data-schedule-field="off" />
          </label>
          <label title="Hard safety limit: above this temperature, command maximum fan speed and use LED protection as configured.">Maximum temperature
            <input type="number" min="5" max="50" step="0.5" placeholder="Off" value="${temperature.max_temp_c ?? ""}" data-temperature-phase="${id}" aria-label="${phase.name || `Phase ${id}`} maximum temperature in Celsius" />
          </label>
          <div class="soft-temperature-limits">
            <strong>Soft temperature limits for this stage</strong>
            <label class="soft-temperature-toggle" title="When temperature exceeds the soft maximum, fan boost starts at 5 percentage points and rises by 10 points each minute while temperature is flat or rising. It backs off only after temperature falls. The boost stops at the stage maximum, global temperature weight, or fan limit.">
              <input type="checkbox" ${softTemperature.enabled === false ? "" : "checked"} data-soft-temperature-enabled-phase="${id}" />
              Enable soft temperature fan boost
            </label>
            <label>Minimum °C
              <input type="number" min="5" max="50" step="0.5" placeholder="Off" value="${softTemperature.min_temp_c ?? ""}" data-soft-temperature-phase="${id}" data-soft-temperature-field="min_temp_c" aria-label="${phase.name || `Phase ${id}`} soft minimum temperature in Celsius" />
            </label>
            <label>Maximum °C
              <input type="number" min="5" max="50" step="0.5" placeholder="Off" value="${softTemperature.max_temp_c ?? ""}" data-soft-temperature-phase="${id}" data-soft-temperature-field="max_temp_c" aria-label="${phase.name || `Phase ${id}`} soft maximum temperature in Celsius" />
            </label>
            <label>Maximum fan boost
              <input type="number" min="0" max="100" step="1" value="${softTemperature.max_boost_pct ?? 20}" data-soft-temperature-phase="${id}" data-soft-temperature-field="max_boost_pct" aria-label="${phase.name || `Phase ${id}`} maximum soft temperature fan boost in percent" />
            </label>
            <span>Caps the added fan speed in percentage points. The global temperature weight and stage fan limit also apply.</span>
          </div>
          <p>The hard maximum temperature remains a separate safety override. Day runs from lights on to lights off, including overnight schedules.</p>
        </div>
        ${rows}`;
      root.appendChild(card);
      syncSoftTemperatureControls(card);
    });
    root.querySelectorAll("[data-reset-stage]").forEach((button) => {
      button.addEventListener("click", () =>
        withBusy(async () => {
          const phaseId = button.dataset.resetStage;
          const phase = state.phases.find((item) => String(item.id ?? 0) === phaseId);
          const phaseName = phase?.name || `Phase ${phaseId}`;
          const warning = state.controlDirty ? " Unsaved Control tab edits will be discarded." : "";
          if (!(await confirmAction(`Reset ${phaseName} to its factory stage defaults? Other stages will stay unchanged.${warning}`))) return;
          const result = await api("/api/control/reset-stage", {
            method: "POST",
            body: JSON.stringify({ phase_id: Number(phaseId) }),
          });
          state.phases = result.phases || state.phases;
          state.controlDirty = false;
          state.controlHydrated = false;
          renderControl(
            { config: result.config, state: result.state, history: state.history },
            { forceForm: true },
          );
          toast(`${phaseName} reset to defaults`);
        }),
      );
    });
    updateCurrentStageCard();
    root.querySelectorAll(".led-slider input[type=range]").forEach((input) => {
      input.addEventListener("input", () => {
        const val = input.parentElement.querySelector(".led-val");
        if (val) val.textContent = `${Math.round(Number(input.value))}%`;
        markControlDirty();
      });
    });
    root.querySelectorAll(".led-val").forEach((button) => {
      button.addEventListener("click", () => editLedValue(button));
      button.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          editLedValue(button);
        }
      });
    });
    root.querySelectorAll("input[data-temperature-phase]").forEach((input) => {
      input.addEventListener("input", markControlDirty);
    });
    bindDualRanges(root);
  }

  function syncSoftTemperatureControls(container) {
    container.querySelectorAll("[data-soft-temperature-enabled-phase]").forEach((toggle) => {
      const limits = toggle.closest(".soft-temperature-limits");
      if (!limits) return;
      const sync = () => {
        limits.classList.toggle("is-disabled", !toggle.checked);
        limits.querySelectorAll("input[data-soft-temperature-phase]").forEach((input) => {
          input.disabled = !toggle.checked;
        });
      };
      toggle.addEventListener("change", () => {
        sync();
        markControlDirty();
      });
      sync();
    });
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

  function collectTemperatureLimits() {
    const out = {};
    document.querySelectorAll("#fanLimitsRoot input[data-temperature-phase]").forEach((el) => {
      const value = el.value.trim();
      if (value !== "") out[el.dataset.temperaturePhase] = { max_temp_c: Number(value) };
    });
    return out;
  }

  function collectSoftTemperatureLimits() {
    const out = {};
    document.querySelectorAll("#fanLimitsRoot input[data-soft-temperature-enabled-phase]").forEach((el) => {
      out[el.dataset.softTemperatureEnabledPhase] = { enabled: el.checked };
    });
    document.querySelectorAll("#fanLimitsRoot input[data-soft-temperature-phase]").forEach((el) => {
      const value = el.value.trim();
      if (value === "") return;
      const phaseId = el.dataset.softTemperaturePhase;
      out[phaseId] = out[phaseId] || {};
      out[phaseId][el.dataset.softTemperatureField] = Number(value);
    });
    return out;
  }

  function collectControlPayload() {
    return {
      enabled: $("pidEnabled").checked,
      manual_mode: $("manualMode").checked,
      manual_fan_pct: Number($("manualFanPct").value),
      manual_led_pct: Number($("manualLedPct").value),
      vpd_targets: collectVpdTargets(),
      pid: {
        kp: Number($("pidKp").value),
        ki: Number($("pidKi").value),
        kd: Number($("pidKd").value),
        ramp_pct_per_min: Number($("pidRamp").value),
        interval_s: Number($("pidInterval").value),
        deadband_kpa: Number($("pidDeadband").value),
        temperature_weight_pct_per_c: Number($("pidTemperatureWeight").value),
      },
      fan_limits: collectFanLimits(),
      led: collectLed(),
      phase_schedules: collectPhaseSchedules(),
      temperature_limits: collectTemperatureLimits(),
      temperature_soft_limits: collectSoftTemperatureLimits(),
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
    const cssW = canvas.parentElement?.clientWidth || canvas.clientWidth || 960;
    const cssH = canvas.clientHeight || (window.matchMedia("(max-width: 900px)").matches ? 220 : 300);
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const w = cssW;
    const h = cssH;
    const pad = { t: 20, r: 14, b: 26, l: 42 };
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(8,28,20,0.35)";
    ctx.fillRect(0, 0, w, h);

    const seriesRoot = $("chartSeries");
    if (seriesRoot && !seriesRoot.dataset.ready) {
      seriesRoot.innerHTML = CHART_SERIES.map((series) => `<button type="button" class="chart-series-toggle" data-chart-series="${series.key}" aria-pressed="${!!state.chartVisible[series.key]}" style="--series-color:${series.color}">${series.label}</button>`).join("");
      seriesRoot.dataset.ready = "1";
      seriesRoot.querySelectorAll("[data-chart-series]").forEach((button) => button.addEventListener("click", () => {
        const key = button.dataset.chartSeries;
        state.chartVisible[key] = !state.chartVisible[key];
        button.setAttribute("aria-pressed", String(state.chartVisible[key]));
        drawChart(state.history);
      }));
    } else if (seriesRoot) {
      seriesRoot.querySelectorAll("[data-chart-series]").forEach((button) => button.setAttribute("aria-pressed", String(!!state.chartVisible[button.dataset.chartSeries])));
    }

    document.querySelectorAll("[data-chart-range]").forEach((button) => {
      const active = Number(button.dataset.chartRange) === state.chartHours;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    if (!history || history.length < 2) {
      ctx.fillStyle = "rgba(238,247,240,0.45)";
      ctx.font = "12px Outfit, sans-serif";
      ctx.fillText(window.t("Collecting control history…"), pad.l, h / 2);
      return;
    }

    const availableStart = Number(history[0].t);
    const availableEnd = Number(history[history.length - 1].t);
    const rangeSeconds = state.chartHours * 3600;
    const availableSpan = Math.max(1, availableEnd - availableStart);
    const displayRangeSeconds = Math.min(rangeSeconds, availableSpan);
    const maxOffset = Math.max(0, (availableSpan - rangeSeconds) / 3600);
    state.chartOffsetHours = Math.max(0, Math.min(maxOffset, state.chartOffsetHours));
    const end = availableEnd - state.chartOffsetHours * 3600;
    const start = end - displayRangeSeconds;
    const visible = history.filter((point) => Number(point.t) >= start && Number(point.t) <= end);
    const plotLeft = pad.l;
    const plotRight = w - pad.r;
    const plotW = Math.max(1, plotRight - plotLeft);
    const gap = 28;
    const panelH = (h - pad.t - pad.b - gap) / 2;
    const xOf = (t) => plotLeft + ((t - start) / displayRangeSeconds) * plotW;
    const formatAxisTime = (t) => state.chartHours >= 24
      ? new Date(t * 1000).toLocaleString(window.i18n.locale, { weekday: "short", hour: "2-digit", minute: "2-digit" })
      : displayRangeSeconds < 3600 ? formatClock(t) : formatClock(t).slice(0, 5);

    const drawPanel = (axis, title, top, maxValue, ticks) => {
      const bottom = top + panelH;
      ctx.fillStyle = "rgba(238,247,240,0.7)";
      ctx.font = "600 10px Outfit, sans-serif";
      ctx.fillText(title, plotLeft, top - 7);
      ctx.font = "9px JetBrains Mono, monospace";
      ctx.lineWidth = 1;
      ticks.forEach((value) => {
        const y = bottom - (value / maxValue) * panelH;
        ctx.strokeStyle = "rgba(238,247,240,0.1)";
        ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(plotRight, y); ctx.stroke();
        ctx.fillStyle = "rgba(238,247,240,0.48)";
        ctx.fillText(axis === "fan" ? `${value}%` : value.toFixed(1), 5, y + 4);
      });
      const seriesSet = CHART_SERIES.filter((series) => series.axis === axis && state.chartVisible[series.key]);
      seriesSet.forEach((series) => {
        ctx.beginPath();
        ctx.strokeStyle = series.color;
        ctx.lineWidth = series.dash ? 1.2 : 1.6;
        ctx.setLineDash(series.dash ? [5, 4] : []);
        let started = false;
        visible.forEach((point) => {
          const raw = point[series.key];
          if (raw == null || Number.isNaN(Number(raw))) { started = false; return; }
          const x = xOf(Number(point.t));
          const y = bottom - (Math.max(0, Math.min(maxValue, Number(raw))) / maxValue) * panelH;
          if (!started) { ctx.moveTo(x, y); started = true; }
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
      ctx.setLineDash([]);
      return bottom;
    };

    const fanBottom = drawPanel("fan", window.t("Fan speed · %"), pad.t, 100, [0, 25, 50, 75, 100]);
    const vpdTop = fanBottom + gap;
    const vpdBottom = drawPanel("vpd", window.t("VPD · kPa"), vpdTop, 2.5, [0, 0.5, 1, 1.5, 2, 2.5]);
    const tickCount = Math.min(6, Math.max(2, Math.floor(plotW / 110)));
    ctx.font = "8px JetBrains Mono, monospace";
    for (let i = 0; i <= tickCount; i++) {
      const t = start + (displayRangeSeconds * i) / tickCount;
      const x = xOf(t);
      ctx.strokeStyle = "rgba(238,247,240,0.07)";
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, vpdBottom); ctx.stroke();
      const label = formatAxisTime(t);
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(238,247,240,0.55)";
      ctx.fillText(label, Math.min(plotRight - tw, Math.max(plotLeft, x - tw / 2)), h - 8);
    }
    const first = visible.length ? formatAxisTime(visible[0].t) : "—";
    const last = visible.length ? formatAxisTime(visible[visible.length - 1].t) : "—";
    const summary = $("chartSummary");
    if (summary) summary.textContent = visible.length ? `${first} – ${last} · ${visible.length.toLocaleString(window.i18n.locale)} ${window.t("samples")} · ${history.length.toLocaleString(window.i18n.locale)} ${window.t("retained")}` : window.t("No samples in this time window · full measurement history is saved in SQLite");
    ctx.setLineDash([]);
  }

  function mergeHistory(points) {
    const incoming = (points || []).filter((point) => point && Number.isFinite(Number(point.t)));
    if (!incoming.length) return;
    const first = Number(incoming[0].t);
    const last = state.history.length ? Number(state.history[state.history.length - 1].t) : -Infinity;
    if (!state.history.length || first < Number(state.history[0].t)) {
      const byTime = new Map(state.history.map((point) => [Number(point.t), point]));
      incoming.forEach((point) => byTime.set(Number(point.t), point));
      state.history = Array.from(byTime.values()).sort((a, b) => Number(a.t) - Number(b.t));
    } else {
      state.history.push(...incoming.filter((point) => Number(point.t) > last));
    }
    const cutoff = Date.now() / 1000 - 48 * 3600;
    const firstRetained = state.history.findIndex((point) => Number(point.t) >= cutoff);
    if (firstRetained > 0) state.history.splice(0, firstRetained);
  }

  async function loadChartHistory() {
    const result = await api("/api/control/history");
    mergeHistory(result.history || []);
    state.chartLoaded = true;
    drawChart(state.history);
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
    if (name === "chart") {
      drawChart(state.history);
      if (!state.chartLoaded) loadChartHistory().catch((err) => console.error(err));
    }
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
      await refreshHa();
    } catch (err) {
      console.error(err);
    }
  }

  async function refreshHa() {
    try {
      const ha = await api("/api/ha");
      const banner = $("haBanner");
      const isReplica = ha.role === "replica";
      state.haReadOnly = isReplica;
      banner.hidden = false;
      banner.dataset.role = ha.role;
      banner.replaceChildren();
      const addText = (value) => banner.appendChild(document.createTextNode(value));
      const addHealth = (online) => {
        const status = document.createElement("span");
        status.className = "ha-health";
        status.dataset.state = online ? "online" : "offline";
        status.textContent = online ? "online" : "offline";
        banner.appendChild(status);
      };
      const host = (value) => {
        try {
          const parsed = new URL(value);
          return ["http:", "https:"].includes(parsed.protocol) ? parsed.hostname : "";
        } catch { return ""; }
      };
      const addLink = (value, label, accessibleName) => {
        const linkHost = host(value);
        if (!linkHost) return false;
        const link = document.createElement("a");
        link.href = value;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = label || linkHost;
        link.setAttribute("aria-label", accessibleName || `Open OpenLisaPro at ${linkHost}`);
        banner.appendChild(link);
        return true;
      };
      const ownHost = host(ha.node_url);
      if (isReplica) {
        addText(`REPLICA ${ownHost || "IP unavailable"} · MASTER `);
        if (!addLink(ha.master_url, host(ha.master_url), "Open the master OpenLisaPro UI")) addText("not configured");
        addText(" · ");
        addHealth(ha.master_healthy);
      } else {
        addText(`${ha.configured_role === "replica" ? "BACKUP MASTER" : "MASTER"} ${ownHost || "IP unavailable"} · REPLICA `);
        if (!addLink(ha.replica_url, host(ha.replica_url), "Open the replica OpenLisaPro UI")) addText("unknown");
        addText(" · ");
        addHealth(ha.replica_healthy);
      }
      document.querySelectorAll("#tab-control .control-toolbar input, #tab-control .control-toolbar button, #manualModePanel input, #controlForm input, #controlForm select, #controlForm button, #tab-device input, #tab-device select, #tab-device button").forEach((el) => {
        if (el.dataset.haWasDisabled === undefined) el.dataset.haWasDisabled = el.disabled ? "1" : "0";
        el.disabled = isReplica || el.dataset.haWasDisabled === "1";
      });
      document.querySelectorAll("#tab-control .control-toolbar, #manualModePanel, #controlForm, #tab-device").forEach((el) => el.classList.toggle("ha-readonly", isReplica));
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
      state.silentEnabled = !!silent.enabled;
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
      state.silentEnabled = $("silentEnabled").checked;
      updateControlLive((state.control && state.control.state) || {}, state.control && state.control.config && state.control.config.enabled, state.control && state.control.config && state.control.config.manual_mode);
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
      const errors = [
        res.device_error && `Lisa Pro settings update failed: ${res.device_error}`,
      ].filter(Boolean);
      toast(errors.length ? `Control settings saved; ${errors.join("; ")}` : "Control settings saved");
    })
  );

  $("btnControlReset").addEventListener("click", () =>
    withBusy(async () => {
      const confirmed = await confirmAction(
        "Reset all control settings, restore the default PID state, clear overrides, and restore the growbox factory phase presets?",
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

  $("btnSilentToggle").addEventListener("click", () =>
    withBusy(async () => {
      const result = await api("/api/proxy/silent/toggle", {
        method: "POST",
        body: "{}",
      });
      state.silentEnabled = !!result.enabled;
      $("silentEnabled").checked = state.silentEnabled;
      updateControlLive((state.control && state.control.state) || {}, state.control && state.control.config && state.control.config.enabled, state.control && state.control.config && state.control.config.manual_mode);
      toast(state.silentEnabled ? "Growbox silent schedule enabled" : "Growbox silent schedule disabled");
      await refreshControl();
      await refreshStatus();
    })
  );

  $("controlForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    $("btnControlSave").click();
  });

  $("controlForm").addEventListener("input", markControlDirty);
  $("controlForm").addEventListener("change", markControlDirty);
  $("pidEnabled").addEventListener("change", markControlDirty);
  $("manualMode").addEventListener("change", () => {
    const enabled = $("manualMode").checked;
    $("manualModeFields").classList.toggle("is-disabled", !enabled);
    $("manualFanPct").disabled = !enabled;
    $("manualLedPct").disabled = !enabled;
    $("pidEnabled").disabled = enabled;
    $("pidEnabledLabel").textContent = enabled ? "PID suspended" : "PID enabled";
    markControlDirty();
  });
  [["manualFanPct", "manualFanValue"], ["manualLedPct", "manualLedValue"]].forEach(([inputId, outputId]) => {
    $(inputId).addEventListener("input", () => {
      $(outputId).textContent = `${fmt0($(inputId).value)}%`;
      markControlDirty();
    });
  });
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
  $("checkBuild")?.addEventListener("click", () => checkBuildVersion(true));
  $("flowEventsPrevious")?.addEventListener("click", () => loadFlowEvents(state.flowPage - 1).catch((err) => console.error(err)));
  $("flowEventsNext")?.addEventListener("click", () => loadFlowEvents(state.flowPage + 1).catch((err) => console.error(err)));

  document.querySelectorAll("[data-chart-range]").forEach((button) => button.addEventListener("click", () => {
    state.chartHours = Number(button.dataset.chartRange);
    state.chartOffsetHours = 0;
    drawChart(state.history);
  }));
  $("chartZoomIn")?.addEventListener("click", () => {
    const previous = state.chartHours;
    state.chartHours = Math.max(0.0833, previous / 2);
    state.chartOffsetHours = Math.max(0, state.chartOffsetHours + (state.chartHours - previous) / 2);
    drawChart(state.history);
  });
  $("chartZoomOut")?.addEventListener("click", () => {
    const previous = state.chartHours;
    state.chartHours = Math.min(48, previous * 2);
    state.chartOffsetHours = Math.max(0, state.chartOffsetHours + (state.chartHours - previous) / 2);
    drawChart(state.history);
  });
  $("chartOlder")?.addEventListener("click", () => { state.chartOffsetHours += state.chartHours * 0.8; drawChart(state.history); });
  $("chartNewer")?.addEventListener("click", () => { state.chartOffsetHours = Math.max(0, state.chartOffsetHours - state.chartHours * 0.8); drawChart(state.history); });
  $("chartLatest")?.addEventListener("click", () => { state.chartOffsetHours = 0; drawChart(state.history); });

  window.addEventListener("resize", () => drawChart(state.history));

  (async function boot() {
    try {
      const meta = await api("/api/meta");
      if (meta.device_url) $("deviceUrl").textContent = meta.device_url;
    } catch (_) {}
    checkBuildVersion();
    setInterval(() => checkBuildVersion(), 15 * 60 * 1000);
    await loadFlowEvents().catch((err) => console.error("Could not load controller flow history", err));
    await refreshStatus();
    await loadSettings();
    setInterval(refreshStatus, 2500);
    setInterval(() => refreshControl(), 5000);
  })();
})();
