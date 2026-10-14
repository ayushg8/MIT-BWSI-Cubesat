// MuraltZ site. Every threshold below is copied from cubesat_flight/config.py.
(function () {
  "use strict";

  var REPO = "https://github.com/ayushg8/MIT-BWSI-Cubesat/blob/main/cubesat_flight/";
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text) n.textContent = text;
    return n;
  }
  function h(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function fmt(n) { return n.toLocaleString("en-US"); }

  /* ---------------- State machine ---------------- */
  var STATES = [
    { id: "BOOT", sub: "self-test", dur: 0, file: "states/boot.py",
      what: "Runs once at startup. Checks the IMU, takes a test capture that must decode as a JPEG, checks disk capacity, and tries to reach the ground station. A missing ground station is not fatal: the spacecraft logs autonomous mode and carries on.",
      kv: [["Boot timeout", "30 s"], ["IMU or camera fail", "safe mode"], ["Storage above 98%", "downlink only"], ["Ground station down", "autonomous mode"]] },
    { id: "WAITING", sub: "awaiting pass", dur: 0, file: "main.py",
      what: "Idles until the operator sends start_pass. Keeps listening for ground commands, pushes a state telemetry packet, and pets the watchdog. The grid cell being imaged comes from operator input, not from a clock.",
      kv: [["Exit trigger", "start_pass command"], ["Grid", "8 x 8 coverage cells"], ["Cell source", "operator input"]] },
    { id: "IMAGING", sub: "gated capture", dur: 75, file: "states/imaging.py",
      what: "Every 3 s it reads the IMU and captures only if nadir is locked and the gyro is steady. Each frame goes through the quality gate and is either accepted with a priority tier or rejected with a logged reason.",
      kv: [["Window", "75 s max"], ["Capture interval", "3.0 s (6.0 s above 70 C)"], ["Max images per pass", "20"], ["Nadir lock / release", "45 deg / 55 deg"], ["Stability gate", "< 1.0 rad/s"], ["Blur gate", "variance >= 20.0"], ["Exposure gate", "mean 15 to 240"]] },
    { id: "IDLE", sub: "queue + cleanup", dur: 30, file: "states/idle.py",
      what: "Builds the downlink queue sorted by tier then score, ages old P2 images down to P3, deletes the oldest P3 images when storage is high, and applies any queued ground commands.",
      kv: [["Window", "30 s"], ["P2 aging", "after 2 passes"], ["P3 cleanup above", "80% storage"], ["Queue order", "tier, then score"]] },
    { id: "DOWNLINK", sub: "throttled send", dur: 60, file: "states/downlink.py",
      what: "Sends telemetry, then images highest priority first in 1,200 byte chunks with a one second sleep after each. Every image carries an MD5 header. The ground station ACKs or NACKs; three NACKs mark the image corrupt.",
      kv: [["Window", "60 s max"], ["Throttle", "1,200 B/s"], ["Byte budget", "72,000 B"], ["Retries per image", "3"], ["GCS failures before suspend", "3"], ["Pause before next pass", "10 s"]] },
    { id: "SAFE MODE", sub: "off the main loop", dur: 0, safe: true, file: "states/safe_mode.py",
      what: "Entered on an IMU or camera boot failure, CPU above 80 C, an unrecoverable error, or a ground command. Camera stopped, no imaging, no downlink. The command listener, thermal monitor and watchdog keep running. Exit on resume_normal.",
      kv: [["Thermal trigger", "80 C"], ["Exit", "resume_normal"]] }
  ];
  var CYCLE = ["WAITING", "IMAGING", "IDLE", "DOWNLINK"];
  var smSel = "IMAGING", smLive = null, smTimer = null, layoutKey = "";
  var stageEl = $("sm-stage");

  function drawSM() {
    var portrait = window.matchMedia("(max-width: 640px)").matches;
    var key = portrait ? "p" : "l";
    var svg = el("svg", { viewBox: portrait ? "0 0 340 560" : "0 0 960 250", role: "group", "aria-label": "Flight state machine" });
    var defs = el("defs", {});
    var mk = el("marker", { id: "ah", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
    mk.appendChild(el("path", { d: "M0 0 L10 5 L0 10 z", class: "arrow" }));
    defs.appendChild(mk); svg.appendChild(defs);
    var pos = {}, w, hh;
    if (portrait) {
      w = 200; hh = 54;
      ["BOOT", "WAITING", "IMAGING", "IDLE", "DOWNLINK"].forEach(function (id, i) { pos[id] = { x: 40, y: 16 + i * 88 }; });
      pos["SAFE MODE"] = { x: 40, y: 16 + 5 * 88 + 8 };
    } else {
      w = 150; hh = 64;
      ["BOOT", "WAITING", "IMAGING", "IDLE", "DOWNLINK"].forEach(function (id, i) { pos[id] = { x: 14 + i * 190, y: 40 }; });
      pos["SAFE MODE"] = { x: 14 + 2 * 190, y: 190 };
    }
    function edge(a, b, cls, d) {
      var p = el("path", { d: d, class: "edge " + (cls || ""), "marker-end": "url(#ah)", "data-a": a, "data-b": b });
      svg.appendChild(p); return p;
    }
    var order = ["BOOT", "WAITING", "IMAGING", "IDLE", "DOWNLINK"];
    for (var i = 0; i < 4; i++) {
      var a = pos[order[i]], b = pos[order[i + 1]], d;
      if (portrait) d = "M" + (a.x + w / 2) + " " + (a.y + hh) + " L" + (b.x + w / 2) + " " + (b.y - 3);
      else d = "M" + (a.x + w) + " " + (a.y + hh / 2) + " L" + (b.x - 3) + " " + (b.y + hh / 2);
      edge(order[i], order[i + 1], "", d);
    }
    // loop back DOWNLINK -> WAITING
    var dn = pos["DOWNLINK"], wt = pos["WAITING"], dd;
    if (portrait) dd = "M" + (dn.x + w) + " " + (dn.y + hh / 2) + " L" + (dn.x + w + 44) + " " + (dn.y + hh / 2) + " L" + (dn.x + w + 44) + " " + (wt.y + hh / 2) + " L" + (wt.x + w + 3) + " " + (wt.y + hh / 2);
    else dd = "M" + (dn.x + w / 2) + " " + (dn.y + hh) + " L" + (dn.x + w / 2) + " 140 L" + (wt.x + w / 2) + " 140 L" + (wt.x + w / 2) + " " + (wt.y + hh + 3);
    edge("DOWNLINK", "WAITING", "", dd);
    var lbl = el("text", { class: "elabel", x: portrait ? 246 : 640, y: portrait ? 262 : 134 }, "after 10 s");
    svg.appendChild(lbl);
    // safe-mode dashed edge from IMAGING
    var im = pos["IMAGING"], sm = pos["SAFE MODE"];
    if (portrait) edge("IMAGING", "SAFE MODE", "dash", "M" + (im.x + 20) + " " + (im.y + hh) + " L" + (im.x + 20) + " " + (im.y + hh + 6) + " L" + (im.x - 14) + " " + (im.y + hh + 6) + " L" + (im.x - 14) + " " + (sm.y + hh / 2) + " L" + (sm.x - 3) + " " + (sm.y + hh / 2));
    else edge("IMAGING", "SAFE MODE", "dash", "M" + (im.x + w / 2) + " " + (im.y + hh) + " L" + (im.x + w / 2) + " " + (sm.y - 3));
    if (!portrait) svg.appendChild(el("text", { class: "elabel", x: im.x + w / 2 + 8, y: 178 }, ""));
    STATES.forEach(function (s) {
      var p = pos[s.id];
      var g = el("g", { class: "node" + (s.safe ? " safe" : ""), tabindex: "0", role: "button", "data-id": s.id, "aria-label": s.id + ": " + s.sub });
      g.appendChild(el("rect", { x: p.x, y: p.y, width: w, height: hh, rx: 6 }));
      g.appendChild(el("text", { x: p.x + w / 2, y: p.y + hh / 2 - 2, "text-anchor": "middle" }, s.id));
      g.appendChild(el("text", { class: "sub", x: p.x + w / 2, y: p.y + hh / 2 + 15, "text-anchor": "middle" }, s.sub));
      function pick() { smSel = s.id; paintSM(); }
      g.addEventListener("click", pick);
      g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      svg.appendChild(g);
    });
    stageEl.replaceChildren(svg);
    layoutKey = key;
    paintSM();
  }

  function paintSM() {
    stageEl.querySelectorAll(".node").forEach(function (g) {
      var id = g.getAttribute("data-id");
      g.classList.toggle("sel", id === smSel);
      g.classList.toggle("live", id === smLive);
      g.setAttribute("aria-pressed", id === smSel ? "true" : "false");
    });
    stageEl.querySelectorAll(".edge").forEach(function (p) {
      p.classList.toggle("live", smLive !== null && p.getAttribute("data-b") === smLive);
    });
    var s = STATES.filter(function (x) { return x.id === smSel; })[0];
    var d = $("sm-detail");
    d.replaceChildren();
    d.appendChild(h("h3", "", s.id));
    d.appendChild(h("p", "", s.what));
    var ul = h("ul", "kv");
    s.kv.forEach(function (r) { ul.appendChild(h("li", "", "<span>" + r[0] + "</span><span>" + r[1] + "</span>")); });
    d.appendChild(ul);
    d.appendChild(h("span", "src", 'Source: <a href="' + REPO + s.file + '">' + s.file + "</a>"));
  }

  function runPass(sp) {
    if (smTimer) return;
    var btn = $("run-pass"), clock = $("sm-clock");
    btn.disabled = true;
    var SPEED = typeof sp === "number" ? sp : 12; // 12x: a 75 s imaging window plays in about 6 s
    var steps = [
      { id: "WAITING", real: 5, note: "start_pass received" },
      { id: "IMAGING", real: 75 }, { id: "IDLE", real: 30 }, { id: "DOWNLINK", real: 60 }
    ];
    var total = 0, i = 0, elapsed = 0;
    function begin() {
      if (i >= steps.length) {
        smLive = null; paintSM(); smTimer = null; btn.disabled = false;
        clock.innerHTML = "Pass complete. <b>" + total + " s</b> of configured maximums, then a 10 s pause and back to WAITING.";
        return;
      }
      var s = steps[i]; smLive = s.id; smSel = s.id; paintSM();
      var start = performance.now(), ms = (s.real / SPEED) * 1000, base = total;
      smTimer = setInterval(function () {
        var f = Math.min(1, (performance.now() - start) / ms);
        clock.innerHTML = "<b>" + s.id + "</b> / t = " + Math.round(base + f * s.real) + " s (" + SPEED + "x speed, max " + s.real + " s)";
        if (f >= 1) { clearInterval(smTimer); smTimer = null; total += s.real; i++; begin(); }
      }, 100);
    }
    begin();
  }
  $("run-pass").addEventListener("click", runPass);
  window.matchMedia("(max-width: 640px)").addEventListener("change", drawSM);
  drawSM();

  /* ---------------- Quality gate ---------------- */
  var CAPTIONS = {
    "AyushG_054319": "Wooden surface under side light",
    "AyushG_054605": "Near-black frame",
    "AyushG_055150": "Near-black frame",
    "AyushG_055234": "Near-black frame",
    "AyushG_055240": "Wooden surface with a white object",
    "AyushG_193122": "Person and wall clock, camera inverted",
    "AyushG_193244": "Partial frame of a person, inverted",
    "AyushG_193548": "Group of three, camera inverted",
    "AyushG_193553": "Group of three, camera inverted",
    "AyushG_193558": "Person near a lamp, rotated",
    "AyushG_193603": "Group of three, wall clock behind",
    "AyushG_194130": "Two people, motion blurred",
    "AyushG_194135": "Group of three, wall clock behind",
    "AyushG_194140": "Group of three, wall clock behind",
    "AyushG_194145": "Ceiling light and a vent",
    "AyushG_194150": "Ceiling light, person at frame edge"
  };
  var DATA = null, qgSel = 0, qgFilter = "all", view = "flight";
  function vk() { return view === "flight" ? "flight" : "fullres"; }
  function getR(i) { var c = DATA.captures[i]; return Object.assign({ file: c.file }, c[vk()]); }
  function cap(file) { return CAPTIONS[file.replace(".jpg", "")] || ""; }

  function meter(v, max, lo, hi) {
    var m = h("div", "meter");
    if (lo != null) {
      var b = h("div", "band");
      b.style.left = (lo / max * 100) + "%"; b.style.width = ((hi - lo) / max * 100) + "%";
      m.appendChild(b);
    }
    var mk = h("div", "mark"); mk.style.left = Math.min(100, v / max * 100) + "%"; m.appendChild(mk);
    return m;
  }
  function counts() {
    var acc = 0;
    DATA.captures.forEach(function (c) { if (c[vk()].accepted) acc++; });
    return { acc: acc, rej: DATA.captures.length - acc };
  }
  function renderChrome() {
    var n = counts(), tot = DATA.captures.length, M = DATA.meta;
    var btns = $("qg-filter").querySelectorAll("button");
    btns[0].textContent = "All " + tot; btns[1].textContent = "Accepted " + n.acc; btns[2].textContent = "Rejected " + n.rej;
    var other = view === "flight" ? "fullres" : "flight", o = 0;
    DATA.captures.forEach(function (c) { if (c[other].accepted) o++; });
    var fl = view === "flight" ? n.acc : o, fu = view === "flight" ? o : n.acc;
    $("qg-head").innerHTML = "<b>" + n.acc + " of " + tot + " accepted</b> " + (view === "flight" ? "at flight resolution (640 x 480)" : "at full resolution (4608 x 2592)") + ".";
    $("qg-note").innerHTML = "<b>Why two views.</b> The flight camera captures 640 x 480, so the representative run resizes each original to that and re-encodes it at JPEG quality " + M.jpeg_quality + " before scoring: <b>" + fl + " accepted, " + (tot - fl) + " rejected</b>, every rejection for blur. Scored as 4608 x 2592 originals the same gate accepts only <b>" + fu + "</b>. Laplacian variance depends on resolution, so one threshold cannot suit both. That sensitivity is why 20.0 is still marked uncalibrated.";
    $("hero-pass").textContent = fl + " / " + tot;
  }
  function renderThumbs() {
    var t = $("thumbs"); t.replaceChildren();
    DATA.captures.forEach(function (c, i) {
      var r = getR(i);
      if (qgFilter === "acc" && !r.accepted) return;
      if (qgFilter === "rej" && r.accepted) return;
      var b = h("button", "thumb");
      b.type = "button";
      b.setAttribute("aria-pressed", i === qgSel ? "true" : "false");
      b.setAttribute("aria-label", c.file + ", " + (r.accepted ? "accepted" : "rejected"));
      var im = h("img"); im.src = "media/captures/" + c.file; im.alt = ""; im.loading = "lazy"; im.width = 240; im.height = 135;
      b.appendChild(im);
      b.appendChild(h("span", "tag " + (r.accepted ? "acc" : "rej"), r.accepted ? "ACCEPT" : "REJECT"));
      b.addEventListener("click", function () { qgSel = i; renderThumbs(); renderDetail(); });
      t.appendChild(b);
    });
    if (!t.children.length) t.appendChild(h("p", "muted small", "No captures match this filter."));
  }

  function renderDetail() {
    var c = getR(qgSel), M = DATA.meta, d = $("qg-detail");
    d.replaceChildren();
    var im = h("img", "shot"); im.src = "media/captures/" + c.file; im.alt = cap(c.file) || "Capture";
    d.appendChild(im);
    d.appendChild(h("div", "verdict", "<span class='mono small'>" + c.file + "</span><b class='" + (c.accepted ? "acc" : "rej") + "'>" + (c.accepted ? "ACCEPTED" : "REJECTED: " + c.reason.toUpperCase()) + "</b>"));
    d.appendChild(h("p", "cap", cap(c.file) + ". Scored at " + c.width + " x " + c.height + (view === "flight" ? ", JPEG Q" + c.jpeg_quality + ", " + fmt(c.file_bytes) + " B." : " (original, " + fmt(c.file_bytes) + " B).")));

    var blurPass = c.blur_variance >= M.blur_threshold;
    var k = h("div", "check");
    k.appendChild(h("div", "row", "<span>1. Blur (Laplacian variance)</span><span class='res " + (blurPass ? "pass" : "fail") + "'>" + (blurPass ? "PASS" : "FAIL") + "</span>"));
    var scale = Math.max(40, Math.ceil(Math.min(c.blur_variance, 400) / 10) * 10);
    var mt = meter(c.blur_variance, scale, null);
    var th = h("div", "th"); th.style.left = (M.blur_threshold / scale * 100) + "%"; mt.appendChild(th);
    k.appendChild(mt);
    k.appendChild(h("div", "vals", "variance " + c.blur_variance + " vs threshold &ge; " + M.blur_threshold.toFixed(1) + " (scale 0 to " + scale + ")"));
    d.appendChild(k);

    var reached = c.accepted, expIn = c.brightness_mean_measured >= M.exposure_min && c.brightness_mean_measured <= M.exposure_max;
    k = h("div", "check");
    k.appendChild(h("div", "row", "<span>2. Exposure (mean brightness)</span><span class='res " + (reached ? "pass" : "na") + "'>" + (reached ? "PASS" : "NOT RUN") + "</span>"));
    k.appendChild(meter(c.brightness_mean_measured, 255, M.exposure_min, M.exposure_max));
    k.appendChild(h("div", "vals", "mean " + c.brightness_mean_measured + " vs band " + M.exposure_min + " to " + M.exposure_max + (reached ? "" : ". The gate stops at the first failed check; measured here for reference, " + (expIn ? "inside the band." : "outside the band."))));
    d.appendChild(k);

    k = h("div", "check");
    k.appendChild(h("div", "row", "<span>3. Motion (gyro at capture)</span><span class='res " + (reached ? "pass" : "na") + "'>" + (reached ? "PASS" : "NOT RUN") + "</span>"));
    k.appendChild(h("div", "vals", "gyro " + M.gyro_rate_used.toFixed(1) + " rad/s (no IMU attached, so 0.0) vs limit &lt; " + M.angular_rate_threshold.toFixed(1)));
    d.appendChild(k);

    k = h("div", "check");
    k.appendChild(h("div", "row", "<span>4. Novelty</span><span class='res na'>NO HARD FAIL</span>"));
    k.appendChild(h("div", "vals", "novelty " + M.novelty_used.toFixed(1) + " assumed (new grid cell). It only moves the score."));
    d.appendChild(k);

    k = h("div", "check");
    k.appendChild(h("div", "row", "<span>Combined score</span><span class='res " + (reached ? "pass" : "fail") + "'>" + (reached ? c.score.toFixed(4) : "0.0 (rejected)") + "</span>"));
    k.appendChild(h("div", "vals", "Priority tier: not computed. It needs the coverage tracker's state from a real pass."));
    d.appendChild(k);

    var oc = DATA.captures[qgSel][view === "flight" ? "fullres" : "flight"];
    d.appendChild(h("p", "note", "<b>" + (view === "flight" ? "Full resolution, for comparison." : "Flight resolution, for comparison.") + "</b> Same file at " + oc.width + " x " + oc.height + ": blur variance " + oc.blur_variance + ", " + (oc.accepted ? "accepted" : "rejected") + "."));
  }

  function initQG(data) {
    DATA = data;
    // Downlink queue: accepted flight-resolution captures ranked by the gate's real combined score.
    var acc = data.captures.filter(function (c) { return c.flight.accepted; })
      .sort(function (a, b) { return b.flight.score - a.flight.score; }).slice(0, 6);
    QUEUE = acc.map(function (c, i) { return { tier: i < 2 ? "P1" : i < 4 ? "P2" : "P3", name: c.file, size: c.flight.file_bytes, score: c.flight.score }; });
    dlInit();
    var vb = $("qg-view").querySelectorAll("button");
    vb.forEach(function (b) {
      b.addEventListener("click", function () {
        view = b.getAttribute("data-v");
        vb.forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        var cur = getR(qgSel);
        if ((qgFilter === "acc" && !cur.accepted) || (qgFilter === "rej" && cur.accepted)) qgSel = 0;
        renderChrome(); renderThumbs(); renderDetail();
      });
    });
    var btns = $("qg-filter").querySelectorAll("button");
    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        qgFilter = b.getAttribute("data-f");
        btns.forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        var cur = getR(qgSel);
        if ((qgFilter === "acc" && !cur.accepted) || (qgFilter === "rej" && cur.accepted)) {
          qgSel = DATA.captures.findIndex(function (c, i) { var r = getR(i); return qgFilter === "acc" ? r.accepted : !r.accepted; });
        }
        renderThumbs(); renderDetail();
      });
    });
    renderChrome(); renderThumbs(); renderDetail();
  }
  fetch("data/captures.json").then(function (r) { return r.json(); }).then(initQG).catch(function (err) { console.error(err);
    $("qg-detail").textContent = "Could not load data/captures.json. Serve the docs folder over http.";
  });

  /* ---------------- Imaging gate ---------------- */
  var NADIR = 45, EXIT = 55, GYRO = 1.0, locked = false;
  var CX = 180, CY = 20, R = 170;
  function pt(a, r) { var rad = a * Math.PI / 180; return [CX + r * Math.sin(rad), CY + r * Math.cos(rad)]; }
  function wedge(a, r) {
    var l = pt(-a, r), rr = pt(a, r);
    return "M" + CX + " " + CY + " L" + l[0].toFixed(1) + " " + l[1].toFixed(1) + " A" + r + " " + r + " 0 0 0 " + rr[0].toFixed(1) + " " + rr[1].toFixed(1) + " Z";
  }
  $("cone-lock").setAttribute("d", wedge(NADIR, R));
  $("ring-exit").setAttribute("d", wedge(EXIT, R));
  var p45 = pt(NADIR, R - 22), p55 = pt(EXIT, R + 4);
  $("lbl45").setAttribute("x", p45[0] - 4); $("lbl45").setAttribute("y", p45[1] - 2);
  $("lbl55").setAttribute("x", p55[0] + 4); $("lbl55").setAttribute("y", p55[1] + 12);

  function updateImaging(fromAngleChange) {
    var a = +$("angle").value, g = +$("gyro").value;
    if (!locked && a < NADIR) locked = true;
    else if (locked && a > EXIT) locked = false;
    $("angle-out").innerHTML = a + "&deg;";
    $("gyro-out").textContent = g.toFixed(2) + " rad/s";
    var e = pt(a, R);
    $("needle").setAttribute("x2", e[0].toFixed(1)); $("needle").setAttribute("y2", e[1].toFixed(1));
    $("dial").classList.toggle("locked", locked);
    var band = a >= NADIR && a <= EXIT;
    $("lock-res").textContent = locked ? (band ? "LOCKED (holding through 45 to 55)" : "LOCKED") : (band ? "NOT LOCKED (needs < 45 to engage)" : "NOT LOCKED");
    var stable = g < GYRO;
    $("stab-res").textContent = stable ? "STABLE" : "TOO FAST";
    var go = locked && stable;
    $("cap-pill").classList.toggle("go", go);
    $("cap-res").textContent = go ? "CAPTURE" : "SKIP: " + (!locked && !stable ? "no lock, too fast" : !locked ? "no nadir lock" : "rate too high");
  }
  $("angle").addEventListener("input", updateImaging);
  $("gyro").addEventListener("input", updateImaging);
  var sweepTimer = null;
  $("sweep").addEventListener("click", function () {
    if (sweepTimer) return;
    if (reduced) { // no animation: step through key angles
      [90, 56, 54, 46, 44, 30, 0, 44, 54, 56, 90].forEach(function (v) { $("angle").value = v; updateImaging(); });
      return;
    }
    var a = 90, dir = -1, btn = $("sweep"); btn.disabled = true;
    $("angle").value = 90; locked = false; updateImaging();
    sweepTimer = setInterval(function () {
      a += dir; $("angle").value = a; updateImaging();
      if (a <= 0) dir = 1;
      if (a >= 90 && dir === 1) { clearInterval(sweepTimer); sweepTimer = null; btn.disabled = false; }
    }, 60);
  });
  updateImaging();

  /* ---------------- Downlink ---------------- */
  var CHUNK = 1200, RATE = 1200, WINDOW = 60, BUDGET = RATE * WINDOW;
  // Filled from captures.json in initQG: real filenames and real 640x480 JPEG sizes.
  var QUEUE = [];
  var dlTimer = null, dlSpeed = 15, dlState;
  function dlInit() {
    dlState = { t: 0, bytes: 0, idx: 0, chunkIn: 0, sent: 0, done: false };
    var q = $("queue"); q.replaceChildren();
    QUEUE.forEach(function (it, i) {
      var n = Math.ceil(it.size / CHUNK);
      var row = h("div", "qrow wait");
      row.id = "qr" + i;
      row.appendChild(h("span", "tier", it.tier));
      row.appendChild(h("span", "sz", it.name.replace("AyushG_", "") + " / " + fmt(it.size) + " B"));
      var ch = h("div", "chunks"); ch.setAttribute("aria-hidden", "true");
      for (var j = 0; j < n; j++) ch.appendChild(h("i"));
      row.appendChild(ch);
      row.appendChild(h("span", "st", "WAITING"));
      q.appendChild(row);
    });
    dlPaint();
    $("dl-verdict").textContent = "Queue: the six best accepted captures at 640 x 480, real JPEG sizes. Telemetry bytes are not counted.";
    $("dl-run").disabled = false;
  }
  function dlPaint() {
    var s = dlState;
    $("dl-time").textContent = s.t + " / " + WINDOW + " s";
    $("dl-bytes").textContent = fmt(s.bytes) + " / " + fmt(BUDGET);
    $("dl-imgs").textContent = s.sent;
    $("budget-fill").style.width = (s.bytes / BUDGET * 100) + "%";
    $("budget").setAttribute("aria-valuenow", s.bytes);
  }
  function markRow(i, cls, label) {
    var r = $("qr" + i); r.className = "qrow " + cls; r.querySelector(".st").textContent = label;
  }
  // One tick = one second of link time = one 1,200 byte chunk, as in transfer.py.
  function dlTick() {
    var s = dlState;
    if (s.done) return;
    if (s.t >= WINDOW) { return dlFinish("Window time exhausted."); }
    if (s.idx >= QUEUE.length) { return dlFinish("Queue empty."); }
    var it = QUEUE[s.idx];
    if (s.chunkIn === 0 && s.bytes + it.size > BUDGET) {
      return dlFinish("Budget check: " + fmt(s.bytes) + " + " + fmt(it.size) + " B would pass " + fmt(BUDGET) + ". The loop stops here, like downlink.py.");
    }
    var chunks = $("qr" + s.idx).querySelectorAll(".chunks i");
    if (s.chunkIn === 0) markRow(s.idx, "cur", "SENDING");
    chunks[s.chunkIn].className = "on";
    s.chunkIn++; s.t++;
    if (s.chunkIn >= chunks.length) {
      chunks.forEach(function (c) { c.className = "done"; });
      s.bytes += it.size; s.sent++;
      markRow(s.idx, "sent", "SENT / ACK");
      s.idx++; s.chunkIn = 0;
    }
    dlPaint();
  }
  function dlFinish(msg) {
    var s = dlState; s.done = true;
    clearInterval(dlTimer); dlTimer = null;
    for (var i = s.idx; i < QUEUE.length; i++) markRow(i, "wait", "STAYS QUEUED");
    var left = QUEUE.length - s.idx;
    $("dl-verdict").textContent = msg + " " + s.sent + " images made it down; " + left + " wait for the next pass (P2 images that wait more than 2 passes are demoted to P3).";
    $("dl-run").disabled = false;
    dlPaint();
  }
  function dlStart() {
    if (dlTimer) return;
    if (dlState.done || dlState.t > 0) dlInit();
    $("dl-run").disabled = true;
    $("dl-verdict").textContent = "Downlink running at " + dlSpeed + "x.";
    if (reduced) { while (!dlState.done) dlTick(); return; }
    dlTimer = setInterval(dlTick, 1000 / dlSpeed);
  }
  $("dl-run").addEventListener("click", dlStart);
  $("dl-reset").addEventListener("click", function () { clearInterval(dlTimer); dlTimer = null; dlInit(); });
  $("dl-speed").querySelectorAll("button").forEach(function (b) {
    b.addEventListener("click", function () {
      dlSpeed = +b.getAttribute("data-s");
      $("dl-speed").querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      if (dlTimer) { clearInterval(dlTimer); dlTimer = setInterval(dlTick, 1000 / dlSpeed); }
    });
  });
  dlInit();

  /* ---------------- Hero telemetry ---------------- */
  var TLM = [
    ["BOOT", ["self-test, 30 s timeout", "IMU / camera / disk check"]],
    ["WAITING", ["awaiting start_pass", "ground commands accepted"]],
    ["IMAGING", ["75 s window, 3 s interval", "nadir < 45 deg, gyro < 1.0 rad/s"]],
    ["IDLE", ["30 s queue build + cleanup", "P2 ages to P3 after 2 passes"]],
    ["DOWNLINK", ["60 s window, 1,200 B/s", "72,000 B budget, MD5 per image"]]
  ];
  var tIdx = 0, sat = $("sat"), track = $("tlm-track");
  TLM.forEach(function () { track.appendChild(h("i")); });
  function tlmPaint() {
    $("tlm-state").textContent = TLM[tIdx][0];
    $("tlm-rows").innerHTML = TLM[tIdx][1].map(function (r) { return "<li>" + r + "</li>"; }).join("");
    track.querySelectorAll("i").forEach(function (n, i) { n.classList.toggle("on", i === tIdx); });
  }
  function satAt(t) { // point on the outer ellipse, rotated -10 deg about (280,150)
    var x = 260 * Math.cos(t), y = 84 * Math.sin(t), r = -10 * Math.PI / 180;
    sat.setAttribute("cx", (280 + x * Math.cos(r) - y * Math.sin(r)).toFixed(1));
    sat.setAttribute("cy", (150 + x * Math.sin(r) + y * Math.cos(r)).toFixed(1));
  }
  tIdx = 2; tlmPaint(); satAt(-0.35);
  if (!reduced) {
    var t0 = performance.now();
    setInterval(function () { tIdx = (tIdx + 1) % TLM.length; tlmPaint(); }, 2600);
    (function loop(now) { satAt(-0.35 + ((now - t0) / 24000) * Math.PI * 2); requestAnimationFrame(loop); })(t0);
  }

  // Hooks used by the demo recorder only.
  window.__demo = { runPass: runPass, dlStart: dlStart };
})();
