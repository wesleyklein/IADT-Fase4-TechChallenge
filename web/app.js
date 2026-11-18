"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  view: "capture",
  session: null,
  busy: false,
  recording: null,
  camera: null,
  alerts: [],
  clips: { video: null, audio: null },
  urls: {},
  vital: { hr: [], spo2: [] },
};
const labels = {
  sinais_vitais: "Sinais vitais",
  prescricao: "Prescrição",
  movimento: "Movimento",
  audio_texto: "Áudio e texto",
  monitor: "Amostra de sinais vitais",
  video: "Vídeo de exercício",
  audio: "Áudio de consulta",
  texto: "Texto de referência",
  video_analysis: "Análise de movimento",
  audio_text_analysis: "Análise de áudio e texto",
};
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
const number = (value) =>
  new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(value);
const timeLabel = (value) => new Date(value).toLocaleTimeString("pt-BR");
const percent = (value) => number(value * 100) + "%";
const json = (data) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

async function api(url, options) {
  const response = await fetch(url, options);
  const content = await response.json();
  if (!response.ok)
    throw new Error(
      typeof content.detail === "string"
        ? content.detail
        : "Não foi possível concluir esta operação.",
    );
  return content;
}

function notice(message = "", error = false) {
  const element = $("notice");
  element.hidden = !message;
  element.classList.toggle("error", error);
  element.textContent = message;
}

function controls() {
  for (const kind of ["video", "audio"]) {
    const active = state.recording?.kind === kind;
    $(kind + "-record").disabled =
      state.busy || Boolean(state.recording && !active);
    $(kind + "-record").classList.toggle("recording", active);
    $(kind + "-record").textContent = active
      ? "Parar gravação"
      : kind === "video"
        ? state.camera
          ? "Gravar vídeo"
          : "Ativar câmera"
        : "Ativar microfone e gravar";
    $(kind + "-analyze").disabled =
      state.busy ||
      Boolean(state.recording) ||
      !state.clips[kind] ||
      (kind === "video" && Boolean(state.camera));
    $(kind + "-file").disabled = state.busy || Boolean(state.recording);
  }
  $("close-camera").hidden = !state.camera || Boolean(state.recording);
  $("close-camera").disabled = state.busy;
  $("new-session").disabled = state.busy || Boolean(state.recording);
  $("demo-start").disabled = state.busy;
  for (const button of document.querySelectorAll(".nav"))
    button.disabled = state.busy || Boolean(state.recording);
}

async function working(action) {
  state.busy = true;
  controls();
  try {
    await action();
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
    controls();
  }
}

function showSession(session) {
  state.session = session;
  localStorage.setItem("iadt-session-" + state.view, session.id);
  $("patient").textContent = session.scenario
    ? "Cenário · " + (session.scenario === "normal" ? "Normal" : "Com desvios")
    : "Sessão " + session.patient_id;
  $("session-mode").textContent = session.scenario
    ? "Reprodução sintética"
    : "Gravação local";
  renderAlerts(session.alerts || []);
}

function resetPreview(kind) {
  if (kind === "video") releaseCamera();
  if (state.urls[kind]) URL.revokeObjectURL(state.urls[kind]);
  state.urls[kind] = null;
  state.clips[kind] = null;
  const media = $(kind + "-preview");
  media.removeAttribute("src");
  media.srcObject = null;
  media.hidden = true;
  $(kind + "-file").value = "";
  if (kind === "video") {
    $("video-placeholder").hidden = false;
    $("video-file-label").textContent = "Câmera desligada";
  } else {
    $("audio-record-status").textContent = "Microfone desligado";
    $("audio-file-label").textContent =
      "O áudio será enviado ao Azure ao analisar.";
  }
}

function resetDemo() {
  state.vital = { hr: [], spo2: [] };
  for (const key of ["hr", "spo2", "bp"]) $(key).textContent = "—";
  for (const key of ["hr", "spo2"]) chart($(key + "-chart"), []);
  $("timeline").replaceChildren();
  $("demo-progress").textContent = "Aguardando reprodução";
  $("demo-result").className = "empty";
  $("demo-result").textContent =
    "Os resultados são exibidos ao concluir o fluxo.";
}

async function createCapture() {
  showSession(await api("/api/sessions", json({})));
  for (const kind of ["video", "audio"]) {
    resetPreview(kind);
    emptyResult(kind);
  }
  notice("Nova sessão aberta.");
}

function emptyResult(kind) {
  const target = $(kind + "-result");
  target.className = "empty";
  target.textContent =
    kind === "video"
      ? "Analise um vídeo para visualizar os ângulos e os intervalos detectados."
      : "A transcrição, os indicadores acústicos e os termos aparecem aqui.";
}

async function setView(view) {
  releaseCamera();
  state.view = view;
  state.session = null;
  const demo = view === "demo";
  $("capture-view").hidden = demo;
  $("demo-view").hidden = !demo;
  $("new-session").hidden = demo;
  $("page-title").innerHTML = demo
    ? "Cenários de<br><em>monitoramento.</em>"
    : "Gravação<br><em>e análise.</em>";
  $("page-subtitle").textContent = demo
    ? "Acompanhe os eventos até a confirmação do alerta."
    : "Grave um exercício e uma fala para examinar as evidências.";
  $("patient").textContent = demo
    ? "Selecione um cenário"
    : "Preparando sessão…";
  $("session-mode").textContent = demo
    ? "Reprodução sintética"
    : "Gravação local";
  for (const button of document.querySelectorAll(".nav"))
    button.classList.toggle("active", button.dataset.view === view);
  notice();
  renderAlerts([]);
  if (demo) resetDemo();
  else
    for (const kind of ["video", "audio"]) {
      resetPreview(kind);
      emptyResult(kind);
    }
  const saved = localStorage.getItem("iadt-session-" + view);
  if (saved) {
    try {
      const session = await api("/api/sessions/" + saved);
      showSession(session);
      if (demo && session.results.demo) {
        $("scenario").value = session.scenario;
        renderDemoResult(session.results.demo);
        $("demo-progress").textContent = "Sessão anterior";
      } else if (!demo) {
        for (const kind of ["video", "audio"]) {
          const result = session.results[kind];
          if (!result) continue;
          setPreview(kind, result.preview_url);
          renderResult(kind, result);
          $(kind + "-file-label").textContent = "Resultado salvo nesta sessão.";
        }
      }
      return;
    } catch {
      localStorage.removeItem("iadt-session-" + view);
    }
  }
  if (!demo) {
    showSession(await api("/api/sessions", json({})));
  }
}

function setPreview(kind, url) {
  const media = $(kind + "-preview");
  media.srcObject = null;
  media.src = url;
  media.muted = false;
  media.controls = true;
  media.hidden = false;
  if (kind === "video") $("video-placeholder").hidden = true;
  else $("audio-record-status").textContent = "Áudio pronto para ouvir";
}

function setClip(kind, file) {
  if (kind === "video") releaseCamera();
  if (file.size > 40 * 1024 * 1024) {
    notice("O limite por arquivo é 40 MB.", true);
    return;
  }
  if (state.urls[kind]) URL.revokeObjectURL(state.urls[kind]);
  state.urls[kind] = URL.createObjectURL(file);
  state.clips[kind] = file;
  setPreview(kind, state.urls[kind]);
  $(kind + "-file-label").textContent =
    file.name + " · " + number(file.size / 1024) + " KB";
  emptyResult(kind);
  notice();
  controls();
}

function waveLine(values) {
  const canvas = $("waveform");
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.strokeStyle = "#82a46e";
  context.lineWidth = 2;
  context.beginPath();
  for (let index = 0; index < values.length; index++) {
    const x = (index / (values.length - 1)) * width;
    const y = (values[index] / 255) * height;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }
  context.stroke();
}

function startWave(recording) {
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  recording.context = new Audio();
  const analyzer = recording.context.createAnalyser();
  analyzer.fftSize = 512;
  recording.context.createMediaStreamSource(recording.stream).connect(analyzer);
  const values = new Uint8Array(analyzer.fftSize);
  function draw() {
    if (state.recording !== recording) return;
    analyzer.getByteTimeDomainData(values);
    waveLine(values);
    requestAnimationFrame(draw);
  }
  draw();
}

function stopRecording() {
  if (state.recording?.recorder.state === "recording")
    state.recording.recorder.stop();
}

function releaseCamera() {
  state.camera?.getTracks().forEach((track) => track.stop());
  state.camera = null;
}

function closeCamera() {
  releaseCamera();
  if (state.urls.video) setPreview("video", state.urls.video);
  else resetPreview("video");
  notice();
  controls();
}

async function previewCamera() {
  await working(async () => {
    try {
      state.camera = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      });
      const video = $("video-preview");
      video.removeAttribute("src");
      video.srcObject = state.camera;
      video.muted = true;
      video.controls = false;
      video.hidden = false;
      $("video-placeholder").hidden = true;
      $("video-file-label").textContent =
        "Câmera pronta. Ajuste o enquadramento e clique em Gravar vídeo.";
      await video.play();
    } catch {
      closeCamera();
      throw new Error(
        "A câmera não está disponível. Confira a permissão ou envie um vídeo.",
      );
    }
  });
}

async function record(kind) {
  if (state.recording) {
    stopRecording();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    notice(
      "Abra a interface em localhost no Edge ou Chrome, ou use o envio de arquivo.",
      true,
    );
    return;
  }
  if (kind === "video" && !state.camera) {
    await previewCamera();
    return;
  }
  if (kind === "audio" && state.camera) closeCamera();
  await working(async () => {
    let stream;
    try {
      stream =
        kind === "video"
          ? state.camera
          : await navigator.mediaDevices.getUserMedia({ audio: true });
      const formats =
        kind === "video"
          ? ["video/webm;codecs=vp8", "video/mp4"]
          : ["audio/webm;codecs=opus", "audio/mp4"];
      const mimeType = formats.find((format) =>
        MediaRecorder.isTypeSupported(format),
      );
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      const recording = {
        kind,
        stream,
        recorder,
        chunks: [],
        start: Date.now(),
        failed: false,
      };
      state.recording = recording;
      const media = $(kind + "-preview");
      const status = $(kind + "-record-status");
      status.hidden = false;
      media.pause();
      if (kind === "video") {
        media.removeAttribute("src");
        media.srcObject = stream;
        media.muted = true;
        media.controls = false;
        media.hidden = false;
        $("video-placeholder").hidden = true;
        media.play().catch(() => {});
      } else {
        media.hidden = true;
        try {
          startWave(recording);
        } catch {
          /* The recording works without visualization. */
        }
      }
      const tick = () => {
        const seconds = Math.floor((Date.now() - recording.start) / 1000);
        status.textContent = "● Gravando · " + seconds + " s";
      };
      tick();
      recording.interval = setInterval(tick, 250);
      recording.timeout = setTimeout(stopRecording, 60000);
      recorder.ondataavailable = (event) => {
        if (event.data.size) recording.chunks.push(event.data);
      };
      recorder.onerror = () => {
        recording.failed = true;
        stopRecording();
      };
      recorder.onstop = () => {
        clearTimeout(recording.timeout);
        clearInterval(recording.interval);
        stream.getTracks().forEach((track) => track.stop());
        recording.context?.close().catch(() => {});
        state.recording = null;
        waveLine([128, 128]);
        if (kind === "video") status.hidden = true;
        const type =
          recorder.mimeType || recording.chunks[0]?.type || kind + "/webm";
        const blob = new Blob(recording.chunks, { type });
        if (!recording.failed && blob.size) {
          setClip(
            kind,
            new File(
              [blob],
              "gravacao." + (type.includes("mp4") ? "mp4" : "webm"),
              { type },
            ),
          );
          notice("Gravação pronta. Revise e clique em Analisar.");
        } else {
          resetPreview(kind);
          notice(
            "A gravação ficou vazia. Tente novamente ou envie um arquivo.",
            true,
          );
        }
        controls();
      };
      recorder.start(250);
      notice();
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      if (kind === "video") closeCamera();
      if (state.recording) {
        clearInterval(state.recording.interval);
        clearTimeout(state.recording.timeout);
        state.recording.context?.close().catch(() => {});
        state.recording = null;
        resetPreview(kind);
        if (kind === "video") $("video-record-status").hidden = true;
      }
      throw new Error(
        error.name === "NotAllowedError"
          ? "Permita o acesso no navegador ou envie um arquivo."
          : "O dispositivo de gravação não está disponível. Você pode enviar um arquivo.",
      );
    }
  });
}

function chart(canvas, values, fixedRange, threshold) {
  const width = Math.max(100, canvas.clientWidth);
  const height = Math.max(80, canvas.clientHeight);
  const scale = window.devicePixelRatio || 1;
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  const valid = values.filter((value) => value != null);
  const [low, high] = fixedRange || [
    Math.min(...valid, 0),
    Math.max(...valid, 1),
  ];
  const y = (value) =>
    height - 6 - ((value - low) / (high - low)) * (height - 12);
  ctx.strokeStyle = "#e2e9de";
  for (let index = 1; index <= 3; index++) {
    ctx.beginPath();
    ctx.moveTo(0, (height * index) / 4);
    ctx.lineTo(width, (height * index) / 4);
    ctx.stroke();
  }
  if (threshold != null) {
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#a3432f";
    ctx.beginPath();
    ctx.moveTo(0, y(threshold));
    ctx.lineTo(width, y(threshold));
    ctx.stroke();
    ctx.setLineDash([]);
  }
  if (!valid.length) return;
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#4c8053";
  ctx.beginPath();
  let previous = false;
  values.forEach((value, index) => {
    if (value == null) {
      previous = false;
      return;
    }
    const x = 3 + (index / Math.max(1, values.length - 1)) * (width - 6);
    if (previous) ctx.lineTo(x, y(value));
    else ctx.moveTo(x, y(value));
    previous = true;
    if (valid.length === 1) ctx.lineTo(x + 3, y(value));
  });
  ctx.stroke();
}

function metrics(entries) {
  return (
    '<div class="result-metrics">' +
    entries
      .map(
        ([value, label]) =>
          "<div><strong>" +
          escape(value) +
          "</strong><span>" +
          escape(label) +
          "</span></div>",
      )
      .join("") +
    "</div>"
  );
}

function speechResult(audio, text) {
  const terms = text.critical_terms || [];
  const sentiment = {
    mixed: "misto",
    negative: "negativo",
    positive: "positivo",
    neutral: "neutro",
  };
  return (
    metrics([
      [number(audio.words_per_minute), "palavras/min"],
      [audio.pause_count_at_least_200ms, "pausas ≥ 200 ms"],
      [
        number(audio.intensity_p90_minus_p10_db) + " dB",
        "variação de intensidade",
      ],
    ]) +
    "<blockquote>" +
    escape(audio.transcript) +
    "</blockquote>" +
    '<p class="small">Sentimento · ' +
    escape(sentiment[text.sentiment] || text.sentiment) +
    "</p>" +
    '<p class="small">Frases-chave · ' +
    escape(text.keyPhrases.join(", ")) +
    "</p>" +
    '<div class="terms">' +
    terms
      .map(
        (term) =>
          '<span class="term' +
          (term.negated ? " negated" : "") +
          '">' +
          escape(term.term) +
          (term.negated ? " · negado" : " · afirmado") +
          "</span>",
      )
      .join("") +
    (terms.length
      ? ""
      : '<span class="small">Nenhum termo crítico identificado.</span>') +
    "</div>"
  );
}

function renderResult(kind, result) {
  const target = $(kind + "-result");
  target.className = "";
  if (kind === "audio") {
    target.innerHTML = speechResult(result.audio, result.text);
    return;
  }
  const angles = result.samples.map((sample) => sample.angle_deg);
  const detected = angles.filter((angle) => angle != null);
  const insufficient =
    !result.cycles.length ||
    result.cycles.every((cycle) => cycle.status === "insuficiente");
  const status = {
    atingido: "Meta atingida",
    reduzido: "Amplitude reduzida",
    insuficiente: "Pose insuficiente",
  };
  target.innerHTML =
    metrics([
      [
        detected.length ? number(Math.max(...detected)) + "°" : "—",
        "maior ângulo",
      ],
      [percent(result.coverage), "quadros com pose"],
      [insufficient ? "—" : result.findings.length, "janelas com desvio"],
    ]) +
    '<canvas id="angle-chart" class="angle-chart" aria-label="Ângulo do braço ao longo do vídeo"></canvas>' +
    '<p class="small">Ângulo ao longo do vídeo · linha tracejada em 60°</p>' +
    (insufficient
      ? '<p class="small">Para avaliar, grave pelo menos 4 s com ombro, cotovelo e quadril visíveis.</p>'
      : "") +
    '<ul class="cycle-list">' +
    result.cycles
      .map(
        (cycle) =>
          "<li><span>" +
          cycle.start_s +
          "–" +
          cycle.end_s +
          " s</span>" +
          '<span class="' +
          (cycle.status === "reduzido" ? "bad" : "") +
          '">' +
          status[cycle.status] +
          "</span></li>",
      )
      .join("") +
    "</ul>" +
    (result.snapshot_url
      ? '<img class="pose-image" alt="Quadro do maior ângulo, com pontos de pose destacados" src="' +
        escape(result.snapshot_url) +
        '">'
      : "");
  chart($("angle-chart"), angles, [0, 180], 60);
}

async function analyze(kind) {
  await working(async () => {
    notice(
      kind === "video"
        ? "Estimando a pose quadro a quadro…"
        : "Convertendo o áudio e consultando o Azure…",
    );
    const form = new FormData();
    form.append("file", state.clips[kind]);
    form.append("side", $("side").value);
    form.append("locale", $("locale").value);
    const content = await api(
      "/api/sessions/" + state.session.id + "/analyze/" + kind,
      { method: "POST", body: form },
    );
    setPreview(kind, content.result.preview_url);
    renderResult(kind, content.result);
    renderAlerts(content.alerts);
    notice("Análise concluída.");
  });
}

function evidenceText(evidence) {
  if (evidence.kind === "sinais_vitais") {
    const names = {
      heart_rate: "FC",
      spo2: "SpO₂",
      systolic: "sistólica",
      diastolic: "diastólica",
    };
    return evidence.deviations
      .map((item) => names[item.metric] + " " + item.value + " " + item.unit)
      .join(" · ");
  }
  if (evidence.kind === "prescricao")
    return (
      evidence.item_id +
      " · " +
      evidence.before +
      " → " +
      evidence.after +
      " " +
      evidence.unit
    );
  if (evidence.kind === "audio_texto")
    return (
      evidence.terms.map((term) => term.term).join(", ") +
      " · " +
      number(evidence.words_per_minute) +
      " palavras/min"
    );
  return (
    evidence.start_s + "–" + evidence.end_s + " s · " + evidence.description
  );
}

function renderAlerts(alerts) {
  state.alerts = alerts;
  $("alert-count").textContent = alerts.length;
  const target = $("alerts");
  target.className = alerts.length ? "" : "empty";
  if (!alerts.length) {
    target.textContent = "Nenhum alerta nesta sessão.";
    return;
  }
  target.innerHTML = alerts
    .map((alert) => {
      const confirmed = alert.status === "confirmado";
      const acknowledgment = alert.acknowledgments.at(-1);
      return (
        '<div class="alert' +
        (confirmed ? " confirmed" : "") +
        '"><div class="alert-top"><div><h3>' +
        (confirmed
          ? "Recebimento confirmado"
          : alert.severity === "alta"
            ? "Alerta de prioridade alta"
            : "Alerta de atenção") +
        "</h3><p>" +
        escape(alert.patient) +
        " · " +
        timeLabel(alert.last_event) +
        "</p></div>" +
        (confirmed
          ? ""
          : '<button data-ack="' +
            escape(alert.id) +
            '">Confirmar recebimento</button>') +
        "</div>" +
        (confirmed && acknowledgment
          ? "<p>Confirmado por " +
            escape(acknowledgment.actor) +
            " às " +
            timeLabel(acknowledgment.timestamp) +
            "</p>"
          : "") +
        "<details><summary>" +
        alert.evidence.length +
        " evidência(s)</summary><ul>" +
        alert.evidence
          .map(
            (item) =>
              "<li><strong>" +
              escape(labels[item.kind]) +
              "</strong> · " +
              escape(evidenceText(item)) +
              "</li>",
          )
          .join("") +
        '</ul></details><span class="small">' +
        escape(alert.id) +
        (alert.latency_ms == null
          ? ""
          : " · processamento " + number(alert.latency_ms) + " ms") +
        "</span></div>"
      );
    })
    .join("");
}

function renderDemoResult(report) {
  const target = $("demo-result");
  const root = "/api/examples/" + report.scenario + "/";
  target.className = "";
  target.innerHTML =
    '<p class="small">Vídeo do cenário · ' +
    report.video.findings.length +
    " desvio(s)</p>" +
    '<video class="demo-media" controls playsinline src="' +
    escape(root + "movimento.webm") +
    '"></video>' +
    '<audio class="demo-media" controls src="' +
    escape(root + "consulta.wav") +
    '"></audio>' +
    speechResult(report.audio, report.text);
}

function renderDemoEvent(payload) {
  if (payload.done) {
    renderDemoResult(payload.report);
    $("demo-progress").textContent = "Reprodução concluída";
    renderAlerts(payload.alerts);
    return;
  }
  const event = payload.event;
  if (event.source === "monitor") {
    $("hr").textContent = event.heart_rate?.value ?? "—";
    $("spo2").textContent = event.spo2?.value ?? "—";
    $("bp").textContent =
      event.systolic && event.diastolic
        ? event.systolic.value + "/" + event.diastolic.value
        : "—";
    state.vital.hr.push(event.heart_rate?.value);
    state.vital.spo2.push(event.spo2?.value);
    chart($("hr-chart"), state.vital.hr, [40, 160], 110);
    chart($("spo2-chart"), state.vital.spo2, [80, 100], 94);
  }
  const row = document.createElement("li");
  row.innerHTML =
    "<span>" +
    escape(labels[event.source] || event.source) +
    "</span><time>" +
    timeLabel(event.timestamp) +
    "</time>";
  $("timeline").prepend(row);
  $("demo-progress").textContent =
    "Reproduzindo · " + timeLabel(event.timestamp);
  if (JSON.stringify(payload.alerts) !== JSON.stringify(state.alerts))
    renderAlerts(payload.alerts);
}

async function demo() {
  await working(async () => {
    resetDemo();
    notice();
    const scenario = $("scenario").value;
    const session = await api("/api/sessions", json({ scenario }));
    showSession(session);
    const response = await fetch("/api/sessions/" + session.id + "/replay", {
      method: "POST",
    });
    if (!response.ok) throw new Error((await response.json()).detail);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines.filter(Boolean)) {
        const payload = JSON.parse(line);
        renderDemoEvent(payload);
        completed ||= Boolean(payload.done);
      }
    }
    if (!completed) throw new Error("A reprodução foi interrompida.");
  });
}

for (const kind of ["video", "audio"]) {
  $(kind + "-record").addEventListener("click", () => record(kind));
  $(kind + "-file").addEventListener("change", (event) => {
    if (event.target.files[0]) setClip(kind, event.target.files[0]);
  });
  $(kind + "-analyze").addEventListener("click", () => analyze(kind));
}
for (const button of document.querySelectorAll(".nav")) {
  button.addEventListener("click", () => {
    if (button.dataset.view !== state.view)
      working(() => setView(button.dataset.view));
  });
}
$("new-session").addEventListener("click", () => working(createCapture));
$("demo-start").addEventListener("click", demo);
$("alerts").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-ack]");
  if (!button) return;
  const actor = $("actor").value.trim();
  if (!actor) {
    $("actor").focus();
    notice("Informe o nome de quem recebeu o alerta.", true);
    return;
  }
  button.disabled = true;
  try {
    const result = await api(
      "/api/sessions/" +
        state.session.id +
        "/alerts/" +
        button.dataset.ack +
        "/ack",
      json({ actor }),
    );
    renderAlerts(result.alerts);
    notice("Recebimento confirmado.");
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
});
$("close-camera").addEventListener("click", closeCamera);
window.addEventListener("pagehide", () => {
  releaseCamera();
  state.recording?.stream.getTracks().forEach((track) => track.stop());
});
waveLine([128, 128]);
working(() => setView("capture"));
api("/api/health")
  .then((status) => {
    $("health").textContent =
      (status.pose_ready ? "Pose pronta" : "Modelo pendente") +
      " · " +
      (status.azure_configured ? "Azure configurado" : "Azure pendente");
  })
  .catch(() => {
    $("health").textContent = "Servidor indisponível";
  });
