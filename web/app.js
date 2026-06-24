const ifcFile = document.getElementById("ifcFile");
const fileName = document.getElementById("fileName");
const projectTitle = document.getElementById("projectTitle");
const statusBadge = document.getElementById("statusBadge");
const resetView = document.getElementById("resetView");
const downloadTables = document.getElementById("downloadTables");
const runGravity = document.getElementById("runGravity");
const runSeismic = document.getElementById("runSeismic");
const runXYComparison = document.getElementById("runXYComparison");
const downloadSeismicReport = document.getElementById("downloadSeismicReport");
const reliabilityGroundMotions = document.getElementById("reliabilityGroundMotions");
const reliabilityGroundMotionName = document.getElementById("reliabilityGroundMotionName");
const runReliability = document.getElementById("runReliability");
const groundMotionFile = document.getElementById("groundMotionFile");
const groundMotionName = document.getElementById("groundMotionName");
const toggleAnimation = document.getElementById("toggleAnimation");
const animationTime = document.getElementById("animationTime");
const animationTimeValue = document.getElementById("animationTimeValue");
const animationScale = document.getElementById("animationScale");
const animationScaleValue = document.getElementById("animationScaleValue");
const animationFrameResponse = document.getElementById("animationFrameResponse");
const gravityDemandMetric = document.getElementById("gravityDemandMetric");
const modeScale = document.getElementById("modeScale");
const modeScaleValue = document.getElementById("modeScaleValue");
const canvas = document.getElementById("modelCanvas");
const ctx = canvas.getContext("2d");

const ids = {
  IFCBUILDINGSTOREY: "storeyCount",
  IFCGRID: "gridCount",
  IFCCOLUMN: "columnCount",
  IFCBEAM: "beamCount",
  IFCPROPERTYSET: "psetCount",
  IFCMATERIAL: "materialCount"
};

let currentStats = null;
let currentProcessed = null;
let currentSeismic = null;
let currentXYComparison = null;
let currentReliability = null;
let currentIfcName = null;
let currentIfcText = null;
let currentGroundMotionName = null;
let currentGroundMotionText = null;
let reliabilityGroundMotionSet = [];
let viewRotationX = -0.55;
let viewRotationZ = 0.72;
let viewZoom = 1;
let activeMode = null;
let modeShapeScale = 0.12;
let modalDisplayMode = "deformed";
let activeGravityDemandMetric = "none";
let seismicAnimationFrameIndex = 0;
let seismicAnimationScale = 20;
let seismicAnimationMarkerTime = null;
let seismicAnimationTimer = null;
let isSeismicAnimationPlaying = false;
let isDragging = false;
let lastPointer = null;

function countEntity(text, entity) {
  const pattern = new RegExp(`=\\s*${entity}\\s*\\(`, "gi");
  return (text.match(pattern) || []).length;
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

async function checkBackendStatus() {
  try {
    const response = await fetch("http://127.0.0.1:8000/api/status");
    if (!response.ok) throw new Error("Backend status request failed");
    await response.json();
    setBackendState(true);
  } catch {
    setBackendState(false);
  }
}

function setBackendState(isConnected) {
  setText("backendState", isConnected ? "Connected" : "Not connected");
  setText("backendSummary", isConnected ? "Connected" : "Not connected");
}

function parseIfcText(text) {
  const stats = {};
  Object.keys(ids).forEach((entity) => {
    stats[entity] = countEntity(text, entity);
  });

  stats.IFCWALL = countEntity(text, "IFCWALL") + countEntity(text, "IFCWALLSTANDARDCASE");
  stats.IFCSLAB = countEntity(text, "IFCSLAB");
  stats.analysisLevels = countAnalysisLevels(text);

  const fileSchema = text.match(/FILE_SCHEMA\s*\(\('([^']+)'/i);
  stats.schema = fileSchema ? fileSchema[1] : "IFC";
  return stats;
}

async function processIfcWithBackend(filename, text, gravity = getGravityInputs(), modelOptions = getModelOptions()) {
  try {
    const response = await fetch("http://127.0.0.1:8000/api/ifc/process", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ filename, text, gravity, model_options: modelOptions })
    });

    if (!response.ok) {
      return null;
    }

    return await response.json();
  } catch {
    return null;
  }
}

function seismicPayload(seismicOverrides = {}) {
  return {
    filename: currentIfcName,
    text: currentIfcText,
    ground_motion: {
      filename: currentGroundMotionName,
      text: currentGroundMotionText
    },
    gravity: getGravityInputs(),
    model_options: getModelOptions(),
    seismic: { ...getSeismicInputs(), ...seismicOverrides }
  };
}

async function processSeismicWithBackend(seismicOverrides = {}) {
  try {
    const response = await fetch("http://127.0.0.1:8000/api/ifc/seismic", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(seismicPayload(seismicOverrides))
    });

    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function statsFromBackend(raw) {
  return {
    IFCBUILDINGSTOREY: raw.storeys,
    IFCGRID: raw.grids,
    IFCCOLUMN: raw.columns,
    IFCBEAM: raw.beams,
    IFCWALL: raw.walls,
    IFCSLAB: raw.slabs,
    IFCPROPERTYSET: raw.property_sets,
    IFCMATERIAL: raw.materials,
    analysisLevels: raw.analysis_levels,
    schema: raw.schema
  };
}

function updateStats(stats) {
  Object.entries(ids).forEach(([entity, id]) => {
    setText(id, stats[entity].toLocaleString());
  });
  setText("wallCount", stats.IFCWALL.toLocaleString());
  setText("analysisLevelCount", stats.analysisLevels.toLocaleString());

  const structuralMembers =
    stats.IFCCOLUMN + stats.IFCBEAM + stats.IFCWALL + stats.IFCSLAB;

  setText("analysisState", `${structuralMembers.toLocaleString()} raw structural IFC entities identified`);
  document.getElementById("topologyTag").textContent = `${stats.schema} raw IFC parsed`;
  statusBadge.textContent = "IFC loaded";
  updateProcessedBaseline();
}

function countAnalysisLevels(text) {
  const matches = [...text.matchAll(/IFCBUILDINGSTOREY\s*\([^;]*?\.ELEMENT\.\s*,\s*([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)/gi)];
  if (!matches.length) return 0;
  return matches
    .map((match) => Number(match[1]))
    .filter((value) => Number.isFinite(value) && value >= -1e-6).length;
}

function updateProcessedBaseline() {
  currentProcessed = null;
  currentSeismic = null;
  currentReliability = null;
  activeMode = null;
  downloadTables.disabled = true;
  setText("processedNodes", "Backend pending");
  setText("processedColumns", "Backend pending");
  setText("processedBeams", "Backend pending");
  setText("processedWalls", "Backend pending");
  setText("skippedMembers", "Backend pending");
  renderGravity(null);
  renderModalRows(null);
  renderSeismic(null);
  renderReliability(null);
}

function updateProcessedCounts(processed) {
  if (!processed || !processed.counts) {
    updateProcessedBaseline();
    return;
  }

  currentProcessed = processed;
  downloadTables.disabled = false;
  const counts = processed.counts;
  setText("processedNodes", counts.nodes.toLocaleString());
  setText("processedColumns", counts.columns.toLocaleString());
  setText("processedBeams", counts.beams.toLocaleString());
  setText("processedWalls", counts.walls.toLocaleString());
  setText(
    "skippedMembers",
    `${counts.skipped_columns} columns, ${counts.skipped_beams} beams, ${counts.skipped_walls} walls`
  );
  renderGravity(processed.gravity);
  renderModalRows(processed.modal);
}

function getGravityInputs() {
  return {
    q_dead: Number(document.getElementById("deadLoad").value || 0),
    q_live: Number(document.getElementById("liveLoad").value || 0),
    psi: Number(document.getElementById("psiLoad").value || 0),
    load_case: document.getElementById("loadCase").value
  };
}

function renderGravity(gravity) {
  if (!gravity) {
    setText("gravityStatus", "Backend pending");
    setText("loadCaseState", "-");
    setText("gravityArea", "-");
    setText("gravityFloorLoad", "-");
    setText("gravityTotalLoad", "-");
    setText("gravitySeismicWeight", "-");
    setText("gravityMaxUz", "-");
    setText("gravityMaxUh", "-");
    setText("expansionJointState", "-");
    setText("gravityDemandState", "-");
    setText("gravityDcrState", "-");
    setText("gravityMethod", "Load an IFC model to run gravity analysis.");
    return;
  }

  const status = gravity.status === "completed" ? "Completed" : `Failed (${gravity.ok})`;
  setText("gravityStatus", status);
  setText("loadCaseState", gravity.parameters ? gravity.parameters.load_case_label : "-");
  setText("gravityArea", `${gravity.floor_area_m2.toFixed(2)} m2 x ${gravity.floor_count} floors`);
  setText("gravityFloorLoad", `${gravity.gravity_load_per_floor_kN.toFixed(2)} kN`);
  setText("gravityTotalLoad", `${gravity.total_gravity_load_kN.toFixed(2)} kN`);
  setText("gravitySeismicWeight", `${gravity.seismic_weight_per_floor_kN.toFixed(2)} kN`);
  setText("gravityMaxUz", `${gravity.max_abs_vertical_disp_mm.toExponential(3)} mm`);
  setText("gravityMaxUh", `${gravity.max_horizontal_disp_mm.toExponential(3)} mm`);
  const options = gravity.model_options || {};
  const springState = options.expansion_joint_springs
    ? `Enabled, ${Number(options.expansion_joint_stiffness).toLocaleString()} N/mm, ${options.expansion_joint_spring_count || 0} springs`
    : "Disabled";
  setText("expansionJointState", springState);
  updateGravityDemandState(gravity);
  updateGravityDcrState(gravity);
  setText(
    "gravityMethod",
    `Current method: ${gravity.parameters.load_case_label} gives qG = ${gravity.q_gravity_kN_m2.toFixed(2)} kN/m2 as equivalent vertical nodal loads. Modal/seismic mass uses qD + psi*qL = ${gravity.q_seismic_kN_m2.toFixed(2)} kN/m2.`
  );
}

function getModelOptions() {
  return {
    expansion_joint_springs: document.getElementById("expansionJointSprings").checked,
    expansion_joint_stiffness: Number(document.getElementById("expansionJointStiffness").value || 0)
  };
}

function getSeismicInputs() {
  const analysisDtValue = document.getElementById("analysisDt").value;
  return {
    target_pga_g: Number(document.getElementById("targetPga").value || 0),
    direction: document.getElementById("direction").value,
    damping: Number(document.getElementById("damping").value || 0),
    dt: Number(document.getElementById("groundMotionDt").value || 0.01),
    analysis_dt: analysisDtValue ? Number(analysisDtValue) : null,
    acceleration_unit: document.getElementById("accelerationUnit").value,
    collapse_idr: Number(document.getElementById("collapseIdr").value || 0.04),
    max_subdivisions: Number(document.getElementById("maxSubdivisions").value || 5)
  };
}

function parseNumberList(value) {
  return String(value || "")
    .split(/[,\s;]+/)
    .map((item) => Number(item.trim()))
    .filter((value) => Number.isFinite(value) && value > 0);
}

function erfApprox(x) {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
  return sign * y;
}

function normalCdf(x) {
  return 0.5 * (1 + erfApprox(x / Math.SQRT2));
}

function inverseNormalCdf(p) {
  if (p <= 0) return Infinity;
  if (p >= 1) return -Infinity;

  const a = [-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269, -30.6647980661472, 2.50662827745924];
  const b = [-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197, -13.2806815528857];
  const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184, -2.54973253934373, 4.37466414146497, 2.93816398269878];
  const d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742];
  const plow = 0.02425;
  const phigh = 1 - plow;

  if (p < plow) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > phigh) {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }

  const q = p - 0.5;
  const r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
    (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

function reliabilityIndexFromPf(pf) {
  if (pf <= 0) return Infinity;
  if (pf >= 1) return -Infinity;
  return -inverseNormalCdf(pf);
}

function fitLognormalFragility(points) {
  const usable = points.filter((point) => point.pf > 0 && point.pf < 1 && point.pga > 0);
  if (usable.length < 2) return null;
  const xs = usable.map((point) => Math.log(point.pga));
  const ys = usable.map((point) => inverseNormalCdf(point.pf));
  const xMean = xs.reduce((sum, value) => sum + value, 0) / xs.length;
  const yMean = ys.reduce((sum, value) => sum + value, 0) / ys.length;
  const sxx = xs.reduce((sum, value) => sum + (value - xMean) ** 2, 0);
  const sxy = xs.reduce((sum, value, index) => sum + (value - xMean) * (ys[index] - yMean), 0);
  if (sxx <= 0 || sxy <= 0) return null;
  const slope = sxy / sxx;
  const intercept = yMean - slope * xMean;
  const beta = 1 / slope;
  const theta = Math.exp(-intercept / slope);
  return { theta, beta, slope, intercept };
}

function lognormalPf(pga, fit) {
  if (!fit || pga <= 0 || fit.beta <= 0) return null;
  return normalCdf((Math.log(pga) - Math.log(fit.theta)) / fit.beta);
}

function renderSeismic(seismic) {
  currentSeismic = seismic;
  if (!seismic) {
    setText("roofDisp", "-");
    setText("peakIdr", "-");
    setText("collapseCheck", "-");
    setText("criticalStory", "-");
    setText("solverDiagnostics", "-");
    setText("xyComparisonSummary", "Not run");
    currentXYComparison = null;
    downloadSeismicReport.disabled = true;
    setSeismicJobStatus("Idle", 0);
    stopSeismicAnimation();
    configureSeismicAnimation(null);
    setText("seismicMethod", "Load an IFC model and ground motion record to run seismic response analysis.");
    drawMiniChart("roofDispChart", []);
    drawMiniChart("maxIdrChart", []);
    drawStoryIdrChart("storyIdrChart", []);
    drawProfileChart("peakIdrProfileChart", []);
    drawDisplacementProfileChart("peakDispProfileChart", []);
    drawXYComparisonChart(null);
    return;
  }

  const summary = seismic.summary || {};
  const parameters = seismic.parameters || {};
  const gm = seismic.ground_motion || {};
  setText("roofDisp", `${Number(summary.peak_roof_disp_mm || 0).toExponential(3)} mm`);
  setText("peakIdr", `${Number(summary.peak_idr_percent || 0).toFixed(3)} %`);
  setText("collapseCheck", summary.collapsed ? "Collapse / near-collapse" : "No collapse by IDR");
  setText("criticalStory", summary.critical_story || "-");
  renderSolverDiagnostics(seismic);
  configureSeismicAnimation(seismic.animation);
  setText(
    "seismicMethod",
    `${parameters.direction || "-"} direction, damping ${Number(parameters.damping || 0).toFixed(3)}, target PGA ${Number(parameters.target_pga_g || 0).toFixed(3)}g; record peak ${Number(gm.peak_input || 0).toFixed(3)} ${parameters.acceleration_unit || ""}, scale ${Number(gm.record_scale || 0).toFixed(3)}; ${gm.points || 0} points, ${Number(gm.duration_s || 0).toFixed(2)} s.`
  );
  drawMiniChart("roofDispChart", seismic.series ? seismic.series.roof_disp : [], {
    unit: "mm",
    valueLabel: "roof disp",
    markerTime: seismicAnimationMarkerTime
  });
  drawMiniChart("maxIdrChart", seismic.series ? seismic.series.max_idr : [], {
    unit: "%",
    valueLabel: "max IDR",
    markerTime: seismicAnimationMarkerTime
  });
  drawStoryIdrChart("storyIdrChart", seismic.series ? seismic.series.story_idr : [], {
    markerTime: seismicAnimationMarkerTime
  });
  drawProfileChart("peakIdrProfileChart", seismic.peak_idr_profile || []);
  drawDisplacementProfileChart("peakDispProfileChart", seismic.peak_displacement_profile || []);
  downloadSeismicReport.disabled = false;
}

function setReliabilityStatus(label, progress) {
  setText("reliabilityStatus", label);
  const bar = document.getElementById("reliabilityProgress");
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;
}

async function runReliabilityAnalysis() {
  if (!currentIfcText || !currentIfcName) {
    statusBadge.textContent = "Load IFC first";
    return;
  }
  if (!reliabilityGroundMotionSet.length) {
    statusBadge.textContent = "Load reliability records";
    return;
  }

  const pgaLevels = parseNumberList(document.getElementById("reliabilityPgaLevels").value);
  const idrLimits = parseNumberList(document.getElementById("reliabilityIdrLimits").value);
  const targetPga = Number(document.getElementById("reliabilityTargetPga").value || 0);
  const direction = document.getElementById("reliabilityDirection").value;
  if (!pgaLevels.length || !idrLimits.length || !targetPga) {
    setReliabilityStatus("Check inputs", 0);
    return;
  }

  runReliability.disabled = true;
  statusBadge.textContent = "Running reliability";
  const total = pgaLevels.length * reliabilityGroundMotionSet.length;
  const runs = [];
  let completed = 0;
  const previousGroundMotionName = currentGroundMotionName;
  const previousGroundMotionText = currentGroundMotionText;

  for (const pga of pgaLevels) {
    for (const record of reliabilityGroundMotionSet) {
      completed += 1;
      setReliabilityStatus(`Run ${completed}/${total}: ${record.filename}, ${pga.toFixed(3)}g`, (completed - 1) / total * 100);
      currentGroundMotionName = record.filename;
      currentGroundMotionText = record.text;
      const result = await processSeismicWithBackend({
        direction,
        target_pga_g: pga
      });
      const seismic = result && result.seismic;
      const peakIdr = seismic && seismic.summary ? Number(seismic.summary.peak_idr_percent || 0) : null;
      runs.push({
        record: record.filename,
        pga,
        direction,
        status: seismic ? seismic.status : "failed",
        peak_idr_percent: peakIdr,
        critical_story: seismic && seismic.summary ? seismic.summary.critical_story : null,
      });
    }
  }
  currentGroundMotionName = previousGroundMotionName;
  currentGroundMotionText = previousGroundMotionText;

  const matrix = buildReliabilityMatrix(runs, pgaLevels, idrLimits);
  const fits = {};
  for (const limit of idrLimits) {
    fits[String(limit)] = fitLognormalFragility(matrix.map((row) => ({
      pga: row.pga,
      pf: row.limit_results[String(limit)].pf,
    })));
  }

  currentReliability = {
    direction,
    pgaLevels,
    idrLimits,
    targetPga,
    records: reliabilityGroundMotionSet.map((record) => record.filename),
    runs,
    matrix,
    fits,
  };
  renderReliability(currentReliability);
  runReliability.disabled = false;
  setReliabilityStatus("Completed", 100);
  statusBadge.textContent = "Reliability completed";
}

function buildReliabilityMatrix(runs, pgaLevels, idrLimits) {
  return pgaLevels.map((pga) => {
    const pgaRuns = runs.filter((run) => Math.abs(run.pga - pga) < 1.0e-9 && run.status === "completed" && run.peak_idr_percent != null);
    const limitResults = {};
    for (const limit of idrLimits) {
      const failures = pgaRuns.filter((run) => run.peak_idr_percent > limit).length;
      const total = pgaRuns.length;
      const pf = total ? failures / total : 0;
      limitResults[String(limit)] = {
        failures,
        total,
        pf,
        beta: reliabilityIndexFromPf(pf),
      };
    }
    return { pga, limit_results: limitResults };
  });
}

function renderReliability(reliability) {
  if (!reliability) {
    setText("reliabilityBatchSize", "-");
    setText("reliabilityTargetSummary", "-");
    document.getElementById("reliabilityMatrix").innerHTML = "";
    document.getElementById("reliabilityRuns").innerHTML = "";
    drawFragilityChart(null);
    return;
  }

  setText("reliabilityBatchSize", `${reliability.records.length} records x ${reliability.pgaLevels.length} PGA levels = ${reliability.runs.length} runs`);
  setText("reliabilityTargetSummary", targetReliabilitySummary(reliability));
  renderReliabilityMatrix(reliability);
  renderReliabilityRuns(reliability.runs);
  drawFragilityChart(reliability);
}

function targetReliabilitySummary(reliability) {
  return reliability.idrLimits.map((limit) => {
    const fit = reliability.fits[String(limit)];
    const pf = lognormalPf(reliability.targetPga, fit);
    if (pf == null) return `IDR>${limit}%: fit unavailable`;
    const beta = reliabilityIndexFromPf(pf);
    return `IDR>${limit}% Pf=${pf.toFixed(3)}, beta=${Number.isFinite(beta) ? beta.toFixed(2) : beta}`;
  }).join("; ");
}

function renderReliabilityMatrix(reliability) {
  const header = reliability.idrLimits.map((limit) => `<th>IDR &gt; ${limit}%</th>`).join("");
  const rows = reliability.matrix.map((row) => {
    const cells = reliability.idrLimits.map((limit) => {
      const result = row.limit_results[String(limit)];
      const beta = result.beta;
      const betaText = Number.isFinite(beta) ? beta.toFixed(2) : (beta === Infinity ? "inf" : "-inf");
      return `<td>Pf ${result.pf.toFixed(3)}<br />beta ${betaText}<br />${result.failures}/${result.total}</td>`;
    }).join("");
    return `<tr><th>${row.pga.toFixed(3)}g</th>${cells}</tr>`;
  }).join("");
  document.getElementById("reliabilityMatrix").innerHTML = `<table><thead><tr><th>PGA</th>${header}</tr></thead><tbody>${rows}</tbody></table>`;
}

function renderReliabilityRuns(runs) {
  const rows = runs.map((run) => `
    <tr>
      <td>${escapeHtml(run.record)}</td>
      <td>${run.pga.toFixed(3)}g</td>
      <td>${run.status}</td>
      <td>${run.peak_idr_percent == null ? "-" : run.peak_idr_percent.toFixed(3)}</td>
      <td>${run.critical_story || "-"}</td>
    </tr>
  `).join("");
  document.getElementById("reliabilityRuns").innerHTML = `<table><thead><tr><th>Record</th><th>PGA</th><th>Status</th><th>Peak IDR (%)</th><th>Story</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderSolverDiagnostics(seismic) {
  const diagnostics = seismic.diagnostics || {};
  if (seismic.status === "completed") {
    setText(
      "solverDiagnostics",
      `Completed to ${Number(diagnostics.end_time || 0).toFixed(3)} s; analysis dt ${Number(diagnostics.analysis_dt || 0).toFixed(4)} s`
    );
    return;
  }

  const failedTime = diagnostics.failed_time == null ? "-" : `${Number(diagnostics.failed_time).toFixed(3)} s`;
  const failedElement = diagnostics.failed_element || "-";
  const reason = diagnostics.failed_reason || "solver failed";
  setText("solverDiagnostics", `Failed at ${failedTime}; element ${failedElement}; ${reason}`);
}

function configureSeismicAnimation(animation) {
  const frames = animation && animation.frames ? animation.frames : [];
  const enabled = Boolean(currentProcessed && frames.length);
  toggleAnimation.disabled = !enabled;
  animationTime.disabled = !enabled;
  animationScale.disabled = !enabled;
  animationTime.max = enabled ? String(frames.length - 1) : "0";
  animationTime.value = "0";
  seismicAnimationFrameIndex = 0;
  seismicAnimationScale = Number(animation && animation.scale_hint ? animation.scale_hint : animationScale.value || 20);
  animationScale.value = String(Math.max(1, Math.min(80, seismicAnimationScale)));
  animationScaleValue.textContent = `${animationScale.value}x`;
  toggleAnimation.textContent = "Play animation";
  seismicAnimationMarkerTime = enabled ? Number(frames[0].time || 0) : null;
  animationTimeValue.textContent = enabled ? `${seismicAnimationMarkerTime.toFixed(2)}s` : "-";
  updateAnimationFrameResponse();
  if (currentProcessed) drawTopology(currentProcessed);
}

function stopSeismicAnimation() {
  if (seismicAnimationTimer) {
    clearInterval(seismicAnimationTimer);
    seismicAnimationTimer = null;
  }
  isSeismicAnimationPlaying = false;
  if (toggleAnimation) toggleAnimation.textContent = "Play animation";
}

function toggleSeismicAnimation() {
  if (!currentSeismic || !currentSeismic.animation || !currentSeismic.animation.frames.length) return;
  if (isSeismicAnimationPlaying) {
    stopSeismicAnimation();
    return;
  }

  isSeismicAnimationPlaying = true;
  toggleAnimation.textContent = "Pause animation";
  seismicAnimationTimer = setInterval(() => {
    const frames = currentSeismic.animation.frames;
    seismicAnimationFrameIndex = (seismicAnimationFrameIndex + 1) % frames.length;
    animationTime.value = String(seismicAnimationFrameIndex);
    updateAnimationTimeLabel(seismicAnimationFrameIndex % 3 === 0);
    drawTopology(currentProcessed);
  }, 70);
}

function updateAnimationTimeLabel(refreshCharts = true) {
  const frames = currentSeismic && currentSeismic.animation && currentSeismic.animation.frames;
  const frame = frames && frames[seismicAnimationFrameIndex];
  seismicAnimationMarkerTime = frame ? Number(frame.time || 0) : null;
  animationTimeValue.textContent = frame ? `${seismicAnimationMarkerTime.toFixed(2)}s` : "-";
  updateAnimationFrameResponse();
  if (refreshCharts) refreshSeismicCharts();
}

function updateAnimationFrameResponse() {
  const frames = currentSeismic && currentSeismic.animation && currentSeismic.animation.frames;
  const frame = frames && frames[seismicAnimationFrameIndex];
  if (!frame) {
    animationFrameResponse.textContent = "-";
    return;
  }
  const maxIdr = Number(frame.max_idr_percent || 0);
  const story = frame.critical_story || "-";
  animationFrameResponse.textContent = `t ${Number(frame.time || 0).toFixed(2)} s, max IDR ${maxIdr.toFixed(3)}%, story ${story}`;
}

function refreshSeismicCharts() {
  if (!currentSeismic) return;
  drawMiniChart("roofDispChart", currentSeismic.series ? currentSeismic.series.roof_disp : [], {
    unit: "mm",
    valueLabel: "roof disp",
    markerTime: seismicAnimationMarkerTime
  });
  drawMiniChart("maxIdrChart", currentSeismic.series ? currentSeismic.series.max_idr : [], {
    unit: "%",
    valueLabel: "max IDR",
    markerTime: seismicAnimationMarkerTime
  });
  drawStoryIdrChart("storyIdrChart", currentSeismic.series ? currentSeismic.series.story_idr : [], {
    markerTime: seismicAnimationMarkerTime
  });
}

function setSeismicJobStatus(label, progress) {
  setText("seismicJobStatus", label);
  const bar = document.getElementById("seismicProgress");
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;
}

function drawMiniChart(id, series, options = {}) {
  const host = document.getElementById(id);
  if (!host) return;
  if (!series || !series.length) {
    host.innerHTML = "";
    return;
  }

  const width = 360;
  const height = 150;
  const pad = 18;
  const bottomPad = 28;
  const times = series.map((point) => Number(point.time));
  const values = series.map((point) => Number(point.value));
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const spanT = maxT - minT || 1;
  const spanV = maxV - minV || 1;
  const peakIndex = values.reduce((bestIndex, value, index) => {
    return Math.abs(value) > Math.abs(values[bestIndex]) ? index : bestIndex;
  }, 0);
  const peakAbs = values[peakIndex] || 0;
  const peakX = pad + ((times[peakIndex] - minT) / spanT) * (width - pad * 2);
  const peakY = height - bottomPad - ((values[peakIndex] - minV) / spanV) * (height - pad - bottomPad);
  const markerTime = Number(options.markerTime);
  const hasMarker = Number.isFinite(markerTime) && markerTime >= minT && markerTime <= maxT;
  const markerX = hasMarker
    ? pad + ((markerTime - minT) / spanT) * (width - pad * 2)
    : null;
  const points = series.map((point) => {
    const x = pad + ((Number(point.time) - minT) / spanT) * (width - pad * 2);
      const y = height - bottomPad - ((Number(point.value) - minV) / spanV) * (height - pad - bottomPad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Time history chart">
      <line x1="${pad}" y1="${height - bottomPad}" x2="${width - pad}" y2="${height - bottomPad}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - bottomPad}" stroke="#d7dde5" />
      <polyline points="${points}" fill="none" stroke="#315f9f" stroke-width="2" />
      <circle cx="${peakX.toFixed(1)}" cy="${peakY.toFixed(1)}" r="3.5" fill="#b83b32" />
      ${hasMarker ? `<line x1="${markerX.toFixed(1)}" y1="${pad}" x2="${markerX.toFixed(1)}" y2="${height - bottomPad}" stroke="#111827" stroke-width="1.4" stroke-dasharray="4 4" />
      <text x="${Math.min(width - pad - 42, markerX + 4).toFixed(1)}" y="${pad + 10}" fill="#111827" font-size="11">${markerTime.toFixed(2)}s</text>` : ""}
      <text x="${pad}" y="14" fill="#687482" font-size="11">peak ${peakAbs.toExponential(2)} ${options.unit || ""}</text>
      ${svgTimeTicks(minT, maxT, pad, width, height - bottomPad, height - 14)}
      <text x="${width / 2 - 22}" y="${height - 4}" fill="#344253" font-size="11">Time (s)</text>
    </svg>
  `;
}

function svgTimeTicks(minT, maxT, pad, width, yAxis, labelY) {
  const spanT = maxT - minT || 1;
  return [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const x = pad + ratio * (width - pad * 2);
    const value = minT + ratio * spanT;
    return `<line x1="${x.toFixed(1)}" y1="${yAxis}" x2="${x.toFixed(1)}" y2="${yAxis + 4}" stroke="#9aa8b8" />
    <text x="${(x - 10).toFixed(1)}" y="${labelY}" fill="#687482" font-size="10">${value.toFixed(2)}</text>`;
  }).join("");
}

function svgValueTicks(maxValue, pad, width, yAxis, labelY, formatter) {
  return [0, 0.5, 1].map((ratio) => {
    const x = pad + ratio * (width - pad * 2);
    const value = ratio * maxValue;
    return `<line x1="${x.toFixed(1)}" y1="${yAxis}" x2="${x.toFixed(1)}" y2="${yAxis + 4}" stroke="#9aa8b8" />
    <text x="${(x - 10).toFixed(1)}" y="${labelY}" fill="#687482" font-size="10">${formatter(value)}</text>`;
  }).join("");
}

function drawStoryIdrChart(id, storySeries, options = {}) {
  const host = document.getElementById(id);
  if (!host) return;
  if (!storySeries || !storySeries.length) {
    host.innerHTML = "";
    return;
  }

  const width = 360;
  const legendRows = Math.ceil(storySeries.length / 3);
  const legendHeight = Math.max(26, legendRows * 13 + 8);
  const bottomPad = 30;
  const height = 166 + legendHeight;
  const pad = 24;
  const palette = ["#315f9f", "#0b6f6a", "#a35d00", "#b83b32", "#6f4aa2", "#2f7d68", "#d7a737", "#687482"];
  const allPoints = storySeries.flatMap((story) => story.series || []);
  if (!allPoints.length) {
    host.innerHTML = "";
    return;
  }

  const times = allPoints.map((point) => Number(point.time));
  const values = allPoints.map((point) => Number(point.value));
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.001);
  const spanT = maxT - minT || 1;
  const minV = -maxAbs;
  const maxV = maxAbs;
  const spanV = maxV - minV || 1;
  const markerTime = Number(options.markerTime);
  const hasMarker = Number.isFinite(markerTime) && markerTime >= minT && markerTime <= maxT;
  const markerX = hasMarker
    ? pad + ((markerTime - minT) / spanT) * (width - pad * 2)
    : null;
  const plotTop = legendHeight;
  const plotBottom = height - bottomPad;
  const plotHeight = plotBottom - plotTop;

  const polylines = storySeries.map((story, index) => {
    const color = palette[index % palette.length];
    const points = (story.series || []).map((point) => {
      const x = pad + ((Number(point.time) - minT) / spanT) * (width - pad * 2);
      const y = plotBottom - ((Number(point.value) - minV) / spanV) * plotHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.7" />`;
  }).join("");

  const legend = storySeries.map((story, index) => {
    const color = palette[index % palette.length];
    const x = pad + (index % 3) * 88;
    const y = 14 + Math.floor(index / 3) * 13;
    return `<circle cx="${x}" cy="${y - 3}" r="3" fill="${color}" /><text x="${x + 7}" y="${y}" fill="#687482" font-size="10">Story ${story.story}</text>`;
  }).join("");

  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Story IDR histories chart">
      ${legend}
      <line x1="${pad}" y1="${plotTop + plotHeight / 2}" x2="${width - pad}" y2="${plotTop + plotHeight / 2}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${plotBottom}" x2="${width - pad}" y2="${plotBottom}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${plotTop}" x2="${pad}" y2="${plotBottom}" stroke="#d7dde5" />
      ${polylines}
      ${hasMarker ? `<line x1="${markerX.toFixed(1)}" y1="${plotTop}" x2="${markerX.toFixed(1)}" y2="${plotBottom}" stroke="#111827" stroke-width="1.4" stroke-dasharray="4 4" />` : ""}
      <text x="${width - pad - 62}" y="14" fill="#687482" font-size="11">IDR (%)</text>
      ${svgTimeTicks(minT, maxT, pad, width, plotBottom, height - 14)}
      <text x="${width / 2 - 22}" y="${height - 4}" fill="#344253" font-size="11">Time (s)</text>
    </svg>
  `;
}

function drawProfileChart(id, profile) {
  const host = document.getElementById(id);
  if (!host) return;
  if (!profile || !profile.length) {
    host.innerHTML = "";
    return;
  }

  const width = 360;
  const height = 150;
  const pad = 24;
  const bottomPad = 30;
  const values = profile.map((point) => Number(point.peak_idr_percent || 0));
  const maxV = Math.max(...values, 0.001);
  const stories = profile.map((point) => Number(point.story || 0));
  const minStory = Math.min(...stories);
  const maxStory = Math.max(...stories);
  const spanStory = maxStory - minStory || 1;
  const points = profile.map((point) => {
    const x = pad + (Number(point.peak_idr_percent || 0) / maxV) * (width - pad * 2);
    const y = height - bottomPad - ((Number(point.story || 0) - minStory) / spanStory) * (height - pad - bottomPad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const peak = profile.reduce((best, point) => {
    return Number(point.peak_idr_percent || 0) > Number(best.peak_idr_percent || 0) ? point : best;
  }, profile[0]);

  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Peak IDR profile chart">
      <line x1="${pad}" y1="${height - bottomPad}" x2="${width - pad}" y2="${height - bottomPad}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - bottomPad}" stroke="#d7dde5" />
      <polyline points="${points}" fill="none" stroke="#a35d00" stroke-width="2" />
      ${profile.map((point) => {
        const x = pad + (Number(point.peak_idr_percent || 0) / maxV) * (width - pad * 2);
        const y = height - bottomPad - ((Number(point.story || 0) - minStory) / spanStory) * (height - pad - bottomPad);
        return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#a35d00" />
        <text x="4" y="${(y + 3).toFixed(1)}" fill="#687482" font-size="10">S${point.story}</text>`;
      }).join("")}
      <text x="${pad}" y="14" fill="#687482" font-size="11">peak ${Number(peak.peak_idr_percent || 0).toFixed(3)}% at story ${peak.story || "-"}</text>
      ${svgValueTicks(maxV, pad, width, height - bottomPad, height - 14, (value) => `${value.toFixed(3)}%`)}
      <text x="${width / 2 - 22}" y="${height - 4}" fill="#344253" font-size="11">IDR (%)</text>
      <text x="4" y="${pad - 6}" fill="#687482" font-size="10">Story</text>
    </svg>
  `;
}

function drawDisplacementProfileChart(id, profile) {
  const host = document.getElementById(id);
  if (!host) return;
  if (!profile || !profile.length) {
    host.innerHTML = "";
    return;
  }

  const width = 360;
  const height = 150;
  const pad = 24;
  const bottomPad = 30;
  const values = profile.map((point) => Number(point.peak_disp_mm || 0));
  const maxV = Math.max(...values, 0.001);
  const stories = profile.map((point) => Number(point.story || 0));
  const minStory = Math.min(...stories);
  const maxStory = Math.max(...stories);
  const spanStory = maxStory - minStory || 1;
  const points = profile.map((point) => {
    const x = pad + (Number(point.peak_disp_mm || 0) / maxV) * (width - pad * 2);
    const y = height - bottomPad - ((Number(point.story || 0) - minStory) / spanStory) * (height - pad - bottomPad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const peak = profile.reduce((best, point) => {
    return Number(point.peak_disp_mm || 0) > Number(best.peak_disp_mm || 0) ? point : best;
  }, profile[0]);

  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Peak displacement profile chart">
      <line x1="${pad}" y1="${height - bottomPad}" x2="${width - pad}" y2="${height - bottomPad}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - bottomPad}" stroke="#d7dde5" />
      <polyline points="${points}" fill="none" stroke="#315f9f" stroke-width="2" />
      ${profile.map((point) => {
        const x = pad + (Number(point.peak_disp_mm || 0) / maxV) * (width - pad * 2);
        const y = height - bottomPad - ((Number(point.story || 0) - minStory) / spanStory) * (height - pad - bottomPad);
        return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#315f9f" />
        <text x="4" y="${(y + 3).toFixed(1)}" fill="#687482" font-size="10">S${point.story}</text>`;
      }).join("")}
      <text x="${pad}" y="14" fill="#687482" font-size="11">peak ${Number(peak.peak_disp_mm || 0).toExponential(2)} mm at story ${peak.story || "-"}</text>
      ${svgValueTicks(maxV, pad, width, height - bottomPad, height - 14, (value) => value.toExponential(2))}
      <text x="${width / 2 - 54}" y="${height - 4}" fill="#344253" font-size="11">Peak displacement (mm)</text>
      <text x="4" y="${pad - 6}" fill="#687482" font-size="10">Story</text>
    </svg>
  `;
}

function drawXYComparisonChart(comparison) {
  const host = document.getElementById("xyComparisonChart");
  if (!host) return;
  if (!comparison || !comparison.x || !comparison.y) {
    host.innerHTML = "";
    return;
  }

  const width = 360;
  const height = 150;
  const pad = 34;
  const xSummary = comparison.x.summary || {};
  const ySummary = comparison.y.summary || {};
  const idrX = Number(xSummary.peak_idr_percent || 0);
  const idrY = Number(ySummary.peak_idr_percent || 0);
  const dispX = Number(xSummary.peak_roof_disp_mm || 0);
  const dispY = Number(ySummary.peak_roof_disp_mm || 0);
  const maxIdr = Math.max(idrX, idrY, 0.001);
  const maxDisp = Math.max(dispX, dispY, 0.001);
  const barW = 28;
  const chartH = 78;
  const yBase = height - pad;

  function bar(x, value, maxValue, color, label) {
    const h = (value / maxValue) * chartH;
    return `<rect x="${x}" y="${(yBase - h).toFixed(1)}" width="${barW}" height="${h.toFixed(1)}" fill="${color}" />
    <text x="${x - 2}" y="${height - 12}" fill="#687482" font-size="10">${label}</text>
    <text x="${x - 7}" y="${(yBase - h - 4).toFixed(1)}" fill="#344253" font-size="10">${value.toFixed(3)}</text>`;
  }

  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Independent X Y comparison chart">
      <line x1="${pad}" y1="${yBase}" x2="${width - pad}" y2="${yBase}" stroke="#d7dde5" />
      <text x="${pad}" y="14" fill="#687482" font-size="11">Peak IDR (%) and peak roof displacement (mm)</text>
      ${bar(74, idrX, maxIdr, "#315f9f", "X IDR")}
      ${bar(110, idrY, maxIdr, "#0b6f6a", "Y IDR")}
      ${bar(218, dispX, maxDisp, "#a35d00", "X disp")}
      ${bar(254, dispY, maxDisp, "#b83b32", "Y disp")}
    </svg>
  `;
}

function drawFragilityChart(reliability) {
  const host = document.getElementById("fragilityChart");
  if (!host) return;
  if (!reliability || !reliability.matrix || !reliability.matrix.length) {
    host.innerHTML = "";
    return;
  }

  const width = 360;
  const height = 180;
  const pad = 30;
  const bottomPad = 32;
  const palette = ["#315f9f", "#0b6f6a", "#a35d00", "#b83b32", "#6f4aa2", "#2f7d68"];
  const maxPga = Math.max(...reliability.pgaLevels, reliability.targetPga) * 1.08;
  const minPga = 0;
  const spanPga = maxPga - minPga || 1;
  const plotBottom = height - bottomPad;
  const plotTop = pad;
  const plotH = plotBottom - plotTop;
  const plotW = width - pad * 2;
  const xForPga = (pga) => pad + ((pga - minPga) / spanPga) * plotW;
  const yForPf = (pf) => plotBottom - Math.max(0, Math.min(1, pf)) * plotH;

  const series = reliability.idrLimits.map((limit, index) => {
    const color = palette[index % palette.length];
    const points = reliability.matrix.map((row) => {
      const result = row.limit_results[String(limit)];
      return `<circle cx="${xForPga(row.pga).toFixed(1)}" cy="${yForPf(result.pf).toFixed(1)}" r="3" fill="${color}" />`;
    }).join("");
    const fit = reliability.fits[String(limit)];
    let curve = "";
    if (fit) {
      const curvePoints = [];
      for (let i = 0; i <= 80; i += 1) {
        const pga = minPga + (i / 80) * spanPga;
        const pf = lognormalPf(pga, fit) || 0;
        curvePoints.push(`${xForPga(pga).toFixed(1)},${yForPf(pf).toFixed(1)}`);
      }
      curve = `<polyline points="${curvePoints.join(" ")}" fill="none" stroke="${color}" stroke-width="1.8" />`;
    }
    const legendY = 13 + index * 13;
    return `${curve}${points}<circle cx="${pad}" cy="${legendY - 3}" r="3" fill="${color}" /><text x="${pad + 8}" y="${legendY}" fill="#687482" font-size="10">IDR &gt; ${limit}%</text>`;
  }).join("");

  const targetX = xForPga(reliability.targetPga);
  const xTicks = [0, 0.5, 1].map((ratio) => {
    const pga = ratio * maxPga;
    const x = xForPga(pga);
    return `<line x1="${x.toFixed(1)}" y1="${plotBottom}" x2="${x.toFixed(1)}" y2="${plotBottom + 4}" stroke="#9aa8b8" />
    <text x="${(x - 10).toFixed(1)}" y="${height - 14}" fill="#687482" font-size="10">${pga.toFixed(2)}</text>`;
  }).join("");

  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Fragility chart">
      <line x1="${pad}" y1="${plotBottom}" x2="${width - pad}" y2="${plotBottom}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${plotTop}" x2="${pad}" y2="${plotBottom}" stroke="#d7dde5" />
      <line x1="${pad}" y1="${yForPf(0.5).toFixed(1)}" x2="${width - pad}" y2="${yForPf(0.5).toFixed(1)}" stroke="#e8edf3" />
      <line x1="${targetX.toFixed(1)}" y1="${plotTop}" x2="${targetX.toFixed(1)}" y2="${plotBottom}" stroke="#111827" stroke-dasharray="4 4" />
      ${series}
      ${xTicks}
      <text x="4" y="${plotTop + 4}" fill="#687482" font-size="10">Pf</text>
      <text x="${width / 2 - 20}" y="${height - 4}" fill="#344253" font-size="11">PGA (g)</text>
    </svg>
  `;
}

function renderXYComparison(comparison) {
  currentXYComparison = comparison;
  if (!comparison || !comparison.x || !comparison.y) {
    setText("xyComparisonSummary", "Not run");
    drawXYComparisonChart(null);
    return;
  }

  const xPeak = Number(comparison.x.summary && comparison.x.summary.peak_idr_percent || 0);
  const yPeak = Number(comparison.y.summary && comparison.y.summary.peak_idr_percent || 0);
  const controlling = xPeak >= yPeak ? comparison.x : comparison.y;
  const direction = xPeak >= yPeak ? "X" : "Y";
  const story = controlling.summary ? controlling.summary.critical_story : "-";
  setText("xyComparisonSummary", `${direction} controls; peak IDR ${Math.max(xPeak, yPeak).toFixed(3)}%, story ${story || "-"}`);
  drawXYComparisonChart(comparison);
}

async function runXYComparisonAnalysis() {
  if (!currentIfcText || !currentIfcName) {
    statusBadge.textContent = "Load IFC first";
    return;
  }
  if (!currentGroundMotionText || !currentGroundMotionName) {
    statusBadge.textContent = "Load ground motion";
    return;
  }

  runXYComparison.disabled = true;
  runSeismic.disabled = true;
  statusBadge.textContent = "Running independent X/Y comparison";
  setSeismicJobStatus("Running X direction", 25);
  setText("seismicMethod", "Running independent X/Y comparison. X and Y are separate analyses rebuilt from the same initial model; damage or residual deformation is not carried over.");

  const xResult = await processSeismicWithBackend({ direction: "X" });
  setSeismicJobStatus("Running Y direction", 65);
  const yResult = await processSeismicWithBackend({ direction: "Y" });

  runXYComparison.disabled = false;
  runSeismic.disabled = false;

  if (!xResult || !xResult.seismic || !yResult || !yResult.seismic) {
    statusBadge.textContent = "Independent X/Y comparison failed";
    setSeismicJobStatus("Failed", 100);
    setText("seismicMethod", "Independent X/Y comparison failed. Try a smaller target PGA or analysis dt.");
    return;
  }

  renderXYComparison({ x: xResult.seismic, y: yResult.seismic });
  setSeismicJobStatus("Completed", 100);
  statusBadge.textContent = "Independent X/Y comparison completed";
  setText("seismicMethod", "Independent X/Y comparison completed. X and Y were run from the same initial model state; single-direction charts still show the last individual seismic result.");
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function reportChart(id, title) {
  const chart = document.getElementById(id);
  const svg = chart ? chart.innerHTML : "";
  if (!svg.trim()) return "";
  return `
    <section class="chart-block">
      <h2>${escapeHtml(title)}</h2>
      <div class="chart">${svg}</div>
    </section>
  `;
}

function reportRow(label, value) {
  return `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(value)}</td></tr>`;
}

function buildSeismicReportHtml() {
  if (!currentSeismic) return "";

  const summary = currentSeismic.summary || {};
  const parameters = currentSeismic.parameters || {};
  const gm = currentSeismic.ground_motion || {};
  const counts = currentProcessed && currentProcessed.counts ? currentProcessed.counts : {};
  const modal = currentProcessed && currentProcessed.modal && currentProcessed.modal.modes
    ? currentProcessed.modal.modes.slice(0, 6)
    : [];
  const generated = new Date().toLocaleString();
  const xyRows = currentXYComparison && currentXYComparison.x && currentXYComparison.y
    ? `
      ${reportRow("X peak IDR", `${Number(currentXYComparison.x.summary.peak_idr_percent || 0).toFixed(3)} %`)}
      ${reportRow("X critical story", currentXYComparison.x.summary.critical_story || "-")}
      ${reportRow("Y peak IDR", `${Number(currentXYComparison.y.summary.peak_idr_percent || 0).toFixed(3)} %`)}
      ${reportRow("Y critical story", currentXYComparison.y.summary.critical_story || "-")}
    `
    : reportRow("Independent X/Y comparison", "Not run");
  const reliabilityRows = currentReliability
    ? `
      ${reportRow("Reliability direction", currentReliability.direction)}
      ${reportRow("Ground-motion records", currentReliability.records.length)}
      ${reportRow("PGA levels", currentReliability.pgaLevels.map((value) => `${value}g`).join(", "))}
      ${reportRow("IDR limits", currentReliability.idrLimits.map((value) => `${value}%`).join(", "))}
      ${reportRow("Target PGA", `${currentReliability.targetPga}g`)}
      ${reportRow("Target reliability", targetReliabilitySummary(currentReliability))}
    `
    : reportRow("Reliability / fragility", "Not run");

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>BIM2Struct Seismic Report - ${escapeHtml(projectTitle.textContent || "model")}</title>
  <style>
    body { margin: 32px; color: #1d2733; font-family: Arial, Helvetica, sans-serif; line-height: 1.45; }
    h1 { margin: 0 0 4px; font-size: 26px; }
    h2 { margin: 24px 0 10px; font-size: 18px; }
    .meta { color: #687482; margin-bottom: 22px; }
    table { width: 100%; border-collapse: collapse; margin: 8px 0 18px; font-size: 13px; }
    th, td { border-bottom: 1px solid #d7dde5; padding: 8px 10px; text-align: left; vertical-align: top; }
    th { width: 260px; color: #344253; background: #f8fafc; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .chart-block { break-inside: avoid; margin-bottom: 18px; }
    .chart { border: 1px solid #d7dde5; border-radius: 6px; padding: 8px; background: #fbfcfe; }
    .chart svg { width: 100%; height: auto; max-height: 320px; }
    @media print { body { margin: 18mm; } .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <h1>BIM2Struct Seismic Report</h1>
  <div class="meta">${escapeHtml(projectTitle.textContent || "Model")} · Generated ${escapeHtml(generated)}</div>

  <h2>Model Summary</h2>
  <table>
    ${reportRow("IFC file", currentIfcName || "-")}
    ${reportRow("Nodes", counts.nodes || "-")}
    ${reportRow("Columns", counts.columns || "-")}
    ${reportRow("Beams", counts.beams || "-")}
    ${reportRow("Walls", counts.walls || "-")}
  </table>

  <h2>Seismic Input</h2>
  <table>
    ${reportRow("Ground motion", currentGroundMotionName || "-")}
    ${reportRow("Direction", parameters.direction || "-")}
    ${reportRow("Target PGA", `${Number(parameters.target_pga_g || 0).toFixed(3)} g`)}
    ${reportRow("Record peak", `${Number(gm.peak_input || 0).toFixed(4)} ${parameters.acceleration_unit || ""}`)}
    ${reportRow("Record scale", Number(gm.record_scale || 0).toFixed(4))}
    ${reportRow("Ground motion dt", `${Number(parameters.dt || 0).toFixed(4)} s`)}
    ${reportRow("Analysis dt", `${Number(parameters.analysis_dt || parameters.dt || 0).toFixed(4)} s`)}
    ${reportRow("Damping ratio", Number(parameters.damping || 0).toFixed(3))}
  </table>

  <h2>Seismic Response Summary</h2>
  <table>
    ${reportRow("Peak roof displacement", `${Number(summary.peak_roof_disp_mm || 0).toExponential(3)} mm`)}
    ${reportRow("Peak IDR", `${Number(summary.peak_idr_percent || 0).toFixed(3)} %`)}
    ${reportRow("Critical story", summary.critical_story || "-")}
    ${reportRow("Collapse check", summary.collapsed ? "Collapse / near-collapse" : "No collapse by IDR")}
    ${reportRow("Solver status", currentSeismic.status || "-")}
  </table>

  <h2>Modal Summary</h2>
  <table>
    <tr><th>Mode</th><th>Frequency / Period / Direction</th></tr>
    ${modal.map((mode) => reportRow(`Mode ${mode.mode}`, `${Number(mode.frequency || 0).toFixed(4)} Hz / ${Number(mode.period || 0).toFixed(4)} s / ${mode.direction || "-"}`)).join("")}
  </table>

  <h2>Independent X/Y Comparison</h2>
  <p>X and Y analyses are independent directional cases. The OpenSees model is rebuilt from the same initial state for each direction, so damage, stiffness degradation, and residual deformation from one direction are not carried into the other.</p>
  <table>${xyRows}</table>
  ${reportChart("xyComparisonChart", "Independent X/Y Comparison")}

  <h2>Reliability / Fragility</h2>
  <table>${reliabilityRows}</table>
  ${reportChart("fragilityChart", "Fragility Curves")}

  <div class="grid">
    ${reportChart("roofDispChart", "Roof Displacement Time History")}
    ${reportChart("maxIdrChart", "Maximum IDR Time History")}
    ${reportChart("storyIdrChart", "Story IDR Histories")}
    ${reportChart("peakIdrProfileChart", "Peak IDR Profile")}
    ${reportChart("peakDispProfileChart", "Peak Displacement Profile")}
  </div>
</body>
</html>`;
}

function downloadSeismicReportHtml() {
  const html = buildSeismicReportHtml();
  if (!html) return;
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const name = projectTitle.textContent || "bim2struct";
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  link.href = url;
  link.download = `${name}_seismic_report_${stamp}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function rerunSeismic() {
  if (!currentIfcText || !currentIfcName) {
    statusBadge.textContent = "Load IFC first";
    return;
  }
  if (!currentGroundMotionText || !currentGroundMotionName) {
    statusBadge.textContent = "Load ground motion";
    return;
  }

  stopSeismicAnimation();
  runSeismic.disabled = true;
  statusBadge.textContent = "Running seismic";
  setSeismicJobStatus("Running", 35);
  setText("seismicMethod", "Nonlinear time-history analysis is running. This may take a while...");
  const backendResult = await processSeismicWithBackend();
  runSeismic.disabled = false;

  if (!backendResult || !backendResult.seismic) {
    statusBadge.textContent = "Seismic failed";
    setBackendState(false);
    setSeismicJobStatus("Failed", 100);
    setText("seismicMethod", "Seismic response analysis failed. Check backend logs and ground motion format.");
    return;
  }

  setBackendState(true);
  renderSeismic(backendResult.seismic);
  setText("analysisState", backendResult.message);
  if (backendResult.seismic.status === "completed") {
    setSeismicJobStatus("Completed", 100);
    statusBadge.textContent = "Seismic completed";
  } else {
    setSeismicJobStatus("Failed", 100);
    statusBadge.textContent = "Seismic failed";
  }
}

function updateGravityDemandState(gravity) {
  if (!gravity || !gravity.member_demands || activeGravityDemandMetric === "none") {
    setText("gravityDemandState", activeGravityDemandMetric === "none" ? "Off" : "No demand data");
    return;
  }

  if (activeGravityDemandMetric === "dcr") {
    const summary = gravity.member_demands.dcr_summary || {};
    setText("gravityDemandState", `DCR, max ${Number(summary.max_dcr || 0).toFixed(3)}`);
    return;
  }

  const key = demandValueKey(activeGravityDemandMetric);
  const maxValue = gravity.member_demands.maxima[key] || 0;
  const unit = activeGravityDemandMetric === "moment" ? "Nmm" : "N";
  setText(
    "gravityDemandState",
    `${labelForDemandMetric(activeGravityDemandMetric)}, max ${formatDemandValue(maxValue)} ${unit}`
  );
}

function updateGravityDcrState(gravity) {
  const summary = gravity && gravity.member_demands && gravity.member_demands.dcr_summary;
  if (!summary) {
    setText("gravityDcrState", "-");
    return;
  }
  setText(
    "gravityDcrState",
    `${Number(summary.max_dcr || 0).toFixed(3)} at ${summary.critical_member_type || "-"} ${summary.critical_member_id || "-"}; highlighted in DCR view`
  );
}

function demandValueKey(metric) {
  if (metric === "moment") return "moment_Nmm";
  if (metric === "shear") return "shear_N";
  return "axial_N";
}

function demandRatioKey(metric) {
  if (metric === "dcr") return "dcr_max";
  if (metric === "moment") return "moment_ratio";
  if (metric === "shear") return "shear_ratio";
  return "axial_ratio";
}

function labelForDemandMetric(metric) {
  if (metric === "dcr") return "Demand-capacity ratio";
  if (metric === "moment") return "Bending moment";
  if (metric === "shear") return "Shear force";
  if (metric === "axial") return "Axial force";
  return "None";
}

function formatDemandValue(value) {
  if (!Number.isFinite(value)) return "-";
  if (Math.abs(value) >= 1000000) return value.toExponential(2);
  return value.toFixed(1);
}

async function rerunWithCurrentGravity() {
  if (!currentIfcText || !currentIfcName) {
    statusBadge.textContent = "Load IFC first";
    return;
  }

  runGravity.disabled = true;
  statusBadge.textContent = "Running gravity";
  setText("gravityStatus", "Running...");
  const backendResult = await processIfcWithBackend(
    currentIfcName,
    currentIfcText,
    getGravityInputs(),
    getModelOptions()
  );
  runGravity.disabled = false;

  if (!backendResult) {
    statusBadge.textContent = "Backend error";
    setBackendState(false);
    setText("gravityStatus", "Backend failed");
    return;
  }

  setBackendState(true);
  setText("analysisState", backendResult.message);
  activeMode = null;
  currentStats = statsFromBackend(backendResult.raw);
  updateStats(currentStats);
  updateProcessedCounts(backendResult.processed);
  drawTopology(backendResult.processed);
  statusBadge.textContent = "Gravity updated";
}

function drawTopology(processed) {
  if (!processed || !processed.tables || !processed.tables.nodes.length) {
    drawPlaceholder(currentStats);
    return;
  }

  const originalNodes = processed.tables.nodes;
  const hasAnimation = hasActiveSeismicAnimation();
  const baseNodes = hasAnimation ? getSeismicAnimationNodes(processed) : originalNodes;
  const hasModeShape = !hasAnimation && Boolean(activeMode && activeMode.shape);
  const hasGravityDemand = !hasModeShape && activeGravityDemandMetric !== "none";
  const deformedNodes = hasModeShape ? getDeformedNodes(processed) : baseNodes;
  const primaryNodes = hasModeShape && modalDisplayMode !== "undeformed" ? deformedNodes : baseNodes;
  const boundsNodes = hasAnimation
    ? originalNodes
    : hasModeShape && modalDisplayMode === "overlay"
    ? baseNodes.concat(deformedNodes)
    : primaryNodes;
  const width = canvas.width;
  const height = canvas.height;
  const pad = 54;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(0, 0, width, height);

  const bounds = getBounds(boundsNodes);
  const center = {
    x: (bounds.minX + bounds.maxX) / 2,
    y: (bounds.minY + bounds.maxY) / 2,
    z: (bounds.minZ + bounds.maxZ) / 2
  };
  const modelSize = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, bounds.maxZ - bounds.minZ, 1);
  const scale = ((Math.min(width, height) - pad * 2) / modelSize) * viewZoom;
  const projectedCache = new Map();

  function project(node) {
    const cacheKey = `${node.id}:${node.x}:${node.y}:${node.z}`;
    if (projectedCache.has(cacheKey)) return projectedCache.get(cacheKey);
    const x0 = node.x - center.x;
    const y0 = node.y - center.y;
    const z0 = node.z - center.z;

    const cosZ = Math.cos(viewRotationZ);
    const sinZ = Math.sin(viewRotationZ);
    const x1 = x0 * cosZ - y0 * sinZ;
    const y1 = x0 * sinZ + y0 * cosZ;

    const cosX = Math.cos(viewRotationX);
    const sinX = Math.sin(viewRotationX);
    const y2 = y1 * cosX - z0 * sinX;
    const z2 = y1 * sinX + z0 * cosX;

    const camera = modelSize * 3.8;
    const perspective = camera / Math.max(camera - z2, modelSize * 0.35);
    const point = {
      x: width / 2 + x1 * scale * perspective,
      y: height / 2 - y2 * scale * perspective,
      depth: z2
    };
    projectedCache.set(cacheKey, point);
    return point;
  }

  if (hasModeShape && modalDisplayMode === "overlay") {
    drawStructuralLayer(processed, baseNodes, project, {
      columnColor: "rgba(104, 116, 130, 0.42)",
      beamColor: "rgba(104, 116, 130, 0.42)",
      wallStroke: "rgba(104, 116, 130, 0.36)",
      wallFill: "rgba(104, 116, 130, 0.08)",
      nodeColor: "rgba(104, 116, 130, 0.46)",
      lineDash: [6, 6],
      lineWidthScale: 0.8,
      nodeRadius: 2.4
    });
    drawStructuralLayer(processed, deformedNodes, project, defaultLayerStyle());
  } else {
    drawStructuralLayer(processed, primaryNodes, project, defaultLayerStyle());
  }

  const counts = processed.counts;
  document.getElementById("topologyTag").textContent =
    hasAnimation
      ? `Seismic response animation: ${currentSeismic.animation.direction} direction, scale ${seismicAnimationScale.toFixed(0)}x`
      : hasModeShape
      ? `Mode ${activeMode.mode}: ${activeMode.frequency.toFixed(3)} Hz, scale ${modeShapeScale.toFixed(2)}`
      : hasGravityDemand
        ? `Gravity demand: ${labelForDemandMetric(activeGravityDemandMetric)}`
      : `${counts.nodes} nodes, ${counts.columns + counts.beams + counts.walls} elements`;

  ctx.fillStyle = "#687482";
  ctx.font = "14px Arial";
  ctx.fillText(
    hasModeShape
      ? "Mode shape view. Use scale and display controls, drag to rotate, mouse wheel to zoom."
      : hasAnimation
        ? "Seismic animation view. Floors translate by diaphragm master displacement; drag to rotate, mouse wheel to zoom."
      : hasGravityDemand
        ? "Gravity demand view. Colors show relative member demand from low to high."
      : "Drag to rotate, mouse wheel to zoom. Blue: columns, green: beams, brown: walls.",
    pad,
    height - 22
  );
}

function defaultLayerStyle() {
  return {
    columnColor: "#315f9f",
    beamColor: "#0b6f6a",
    wallStroke: "#a35d00",
    wallFill: "rgba(163, 93, 0, 0.16)",
    nodeColor: "#1d2733",
    lineDash: [],
    lineWidthScale: 1,
    nodeRadius: 3.2
  };
}

function drawStructuralLayer(processed, nodes, project, style) {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const demandMaps = buildDemandMaps(processed);
  const critical = getCriticalDemandMember(processed);
  const drawItems = [];

  function queueLine(n1, n2, color, lineWidth, demandType, elementId) {
    const a = nodeMap.get(n1);
    const b = nodeMap.get(n2);
    if (!a || !b) return;
    const pa = project(a);
    const pb = project(b);
    const demandColor = getDemandColor(demandMaps, demandType, elementId);
    drawItems.push({
      type: "line",
      depth: (pa.depth + pb.depth) / 2,
      pa,
      pb,
      color: demandColor || color,
      lineWidth: lineWidth * style.lineWidthScale,
      critical: isCriticalDemandElement(critical, demandType, elementId)
    });
  }

  for (const wall of processed.tables.walls) {
    const a = nodeMap.get(wall.node1);
    const b = nodeMap.get(wall.node2);
    const c = nodeMap.get(wall.node3);
    const d = nodeMap.get(wall.node4);
    if (!a || !b || !c || !d) continue;
    const points = [project(a), project(b), project(c), project(d)];
    drawItems.push({
      type: "wall",
      depth: points.reduce((sum, point) => sum + point.depth, 0) / points.length,
      points,
      demandColor: getDemandColor(demandMaps, "walls", wall.id),
      critical: isCriticalDemandElement(critical, "walls", wall.id)
    });
  }

  for (const column of processed.tables.columns) {
    queueLine(column.nodeI, column.nodeJ, style.columnColor, 3, "columns", column.id);
  }

  for (const beam of processed.tables.beams) {
    queueLine(beam.nodeI, beam.nodeJ, style.beamColor, 2, "beams", beam.id);
  }

  drawItems.sort((a, b) => a.depth - b.depth);
  ctx.setLineDash(style.lineDash);

  for (const item of drawItems) {
    if (item.type === "wall") {
      ctx.fillStyle = item.demandColor ? demandFillColor(item.demandColor) : style.wallFill;
      ctx.strokeStyle = item.critical ? "#111827" : item.demandColor || style.wallStroke;
      ctx.lineWidth = (item.critical ? 4 : 2) * style.lineWidthScale;
      ctx.beginPath();
      ctx.moveTo(item.points[0].x, item.points[0].y);
      for (const point of item.points.slice(1)) ctx.lineTo(point.x, point.y);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    } else {
      if (item.critical) {
        ctx.strokeStyle = "#111827";
        ctx.lineWidth = item.lineWidth + 5;
        ctx.beginPath();
        ctx.moveTo(item.pa.x, item.pa.y);
        ctx.lineTo(item.pb.x, item.pb.y);
        ctx.stroke();
      }
      ctx.strokeStyle = item.color;
      ctx.lineWidth = item.critical ? item.lineWidth + 1.5 : item.lineWidth;
      ctx.beginPath();
      ctx.moveTo(item.pa.x, item.pa.y);
      ctx.lineTo(item.pb.x, item.pb.y);
      ctx.stroke();
    }
  }

  ctx.setLineDash([]);
  ctx.fillStyle = style.nodeColor;
  for (const node of nodes) {
    const p = project(node);
    ctx.beginPath();
    ctx.arc(p.x, p.y, style.nodeRadius, 0, Math.PI * 2);
    ctx.fill();
  }
}

function buildDemandMaps(processed) {
  const demands = processed.gravity && processed.gravity.member_demands;
  if (!demands || activeGravityDemandMetric === "none" || activeMode) return null;
  return {
    columns: new Map((demands.columns || []).map((item) => [item.id, item])),
    beams: new Map((demands.beams || []).map((item) => [item.id, item])),
    walls: new Map((demands.walls || []).map((item) => [item.id, item]))
  };
}

function getCriticalDemandMember(processed) {
  if (activeGravityDemandMetric !== "dcr") return null;
  const summary = processed.gravity && processed.gravity.member_demands && processed.gravity.member_demands.dcr_summary;
  if (!summary || !summary.critical_member_id || !summary.critical_member_type) return null;
  return {
    id: Number(summary.critical_member_id),
    type: `${summary.critical_member_type}s`
  };
}

function isCriticalDemandElement(critical, demandType, elementId) {
  return Boolean(critical && critical.type === demandType && critical.id === Number(elementId));
}

function getDemandColor(demandMaps, demandType, elementId) {
  if (!demandMaps || !demandMaps[demandType]) return null;
  const demand = demandMaps[demandType].get(elementId);
  if (!demand || !demand.response_available) return null;
  const ratio = Math.max(0, Math.min(1, demand[demandRatioKey(activeGravityDemandMetric)] || 0));
  return demandColorScale(ratio);
}

function demandColorScale(ratio) {
  const stops = [
    { t: 0.0, color: [47, 125, 104] },
    { t: 0.5, color: [215, 167, 55] },
    { t: 1.0, color: [184, 59, 50] }
  ];
  const left = ratio <= 0.5 ? stops[0] : stops[1];
  const right = ratio <= 0.5 ? stops[1] : stops[2];
  const localT = (ratio - left.t) / (right.t - left.t || 1);
  const rgb = left.color.map((value, index) => Math.round(value + (right.color[index] - value) * localT));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function demandFillColor(strokeColor) {
  const values = strokeColor.match(/\d+/g);
  if (!values || values.length < 3) return "rgba(184, 59, 50, 0.18)";
  return `rgba(${values[0]}, ${values[1]}, ${values[2]}, 0.18)`;
}

function getDeformedNodes(processed) {
  const baseNodes = processed.tables.nodes;
  if (!activeMode || !activeMode.shape) return baseNodes;

  const shapeMap = new Map(activeMode.shape.map((shape) => [shape.node, shape]));
  const bounds = getBounds(baseNodes);
  const modelSize = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, bounds.maxZ - bounds.minZ, 1);
  const amplitude = modelSize * modeShapeScale;

  return baseNodes.map((node) => {
    const shape = shapeMap.get(node.id);
    if (!shape) return node;
    return {
      ...node,
      x: node.x + shape.dx * amplitude,
      y: node.y + shape.dy * amplitude,
      z: node.z + shape.dz * amplitude
    };
  });
}

function hasActiveSeismicAnimation() {
  return Boolean(
    currentSeismic &&
    currentSeismic.animation &&
    currentSeismic.animation.frames &&
    currentSeismic.animation.frames.length &&
    currentProcessed
  );
}

function getSeismicAnimationNodes(processed) {
  const animation = currentSeismic && currentSeismic.animation;
  if (!animation || !animation.frames || !animation.frames.length) {
    return processed.tables.nodes;
  }

  const frame = animation.frames[Math.max(0, Math.min(seismicAnimationFrameIndex, animation.frames.length - 1))];
  const floors = animation.floors || [];
  const displacements = frame.floor_displacements_mm || {};
  if (!floors.length) return processed.tables.nodes;

  return processed.tables.nodes.map((node) => {
    const floor = nearestAnimationFloor(node.z, floors);
    const displacement = floor ? Number(displacements[String(floor.story)] || 0) * seismicAnimationScale : 0;
    if (animation.direction === "Y") {
      return { ...node, y: node.y + displacement };
    }
    return { ...node, x: node.x + displacement };
  });
}

function nearestAnimationFloor(z, floors) {
  let best = null;
  let bestDistance = Infinity;
  for (const floor of floors) {
    const floorZ = Number(floor.height_m || 0) * 1000;
    const distance = Math.abs(Number(z) - floorZ);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = floor;
    }
  }
  return best;
}

function renderModalRows(modal) {
  const modalRows = document.getElementById("modalRows");
  if (!modal || !modal.modes || !modal.modes.length) {
    modalRows.innerHTML = `<tr><td colspan="4">Connect OpenSees backend to populate modal results.</td></tr>`;
    return;
  }

  modalRows.innerHTML = "";
  for (const mode of modal.modes) {
    const row = document.createElement("tr");
    row.className = activeMode && activeMode.mode === mode.mode ? "selected-row" : "";
    row.innerHTML = `
      <td><button class="mode-button" type="button" data-mode="${mode.mode}">Mode ${mode.mode}</button></td>
      <td>${mode.frequency.toFixed(4)} Hz</td>
      <td>${mode.period.toFixed(4)} s</td>
      <td>${mode.direction}</td>
    `;
    modalRows.appendChild(row);
  }

  modalRows.querySelectorAll(".mode-button").forEach((button) => {
    button.addEventListener("click", () => {
      stopSeismicAnimation();
      const modeId = Number(button.dataset.mode);
      activeMode = modal.modes.find((mode) => mode.mode === modeId) || null;
      renderModalRows(modal);
      drawTopology(currentProcessed);
    });
  });
}

function getBounds(nodes) {
  return {
    minX: Math.min(...nodes.map((node) => node.x)),
    maxX: Math.max(...nodes.map((node) => node.x)),
    minY: Math.min(...nodes.map((node) => node.y)),
    maxY: Math.max(...nodes.map((node) => node.y)),
    minZ: Math.min(...nodes.map((node) => node.z)),
    maxZ: Math.max(...nodes.map((node) => node.z))
  };
}

function drawPlaceholder(stats) {
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(0, 0, width, height);

  const cx = width / 2;
  const cy = height / 2;
  const boxW = 520;
  const boxH = 168;
  const x = cx - boxW / 2;
  const y = cy - boxH / 2;

  ctx.strokeStyle = "#d7dde5";
  ctx.lineWidth = 2;
  ctx.setLineDash([8, 8]);
  ctx.strokeRect(x, y, boxW, boxH);
  ctx.setLineDash([]);

  ctx.fillStyle = "#1d2733";
  ctx.font = "700 22px Arial";
  ctx.textAlign = "center";
  ctx.fillText("Real topology requires BIM2Struct backend", cx, y + 48);

  ctx.fillStyle = "#687482";
  ctx.font = "15px Arial";
  ctx.fillText("Raw IFC entities are loaded, but nodes and members are not reconstructed in the browser.", cx, y + 84);
  ctx.fillText("Next step: run Python to generate node_table and element tables, then draw them here.", cx, y + 112);

  ctx.fillStyle = "#0b6f6a";
  ctx.font = "700 15px Arial";
  const raw = `${stats.IFCCOLUMN.toLocaleString()} columns, ${stats.IFCBEAM.toLocaleString()} beams, ${stats.IFCWALL.toLocaleString()} walls detected in raw IFC`;
  ctx.fillText(raw, cx, y + 142);
  ctx.textAlign = "left";
}

function drawEmpty() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#687482";
  ctx.font = "18px Arial";
  ctx.fillText("Load an IFC model to inspect raw entities", 58, 80);
  ctx.font = "14px Arial";
  ctx.fillText("Real topology will appear after connecting the BIM2Struct Python backend.", 58, 110);
}

ifcFile.addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;

  fileName.textContent = file.name;
  stopSeismicAnimation();
  renderSeismic(null);
  projectTitle.textContent = file.name.replace(/\.ifc$/i, "");
  statusBadge.textContent = "Reading";

  const text = await file.text();
  currentIfcName = file.name;
  currentIfcText = text;
  const backendResult = await processIfcWithBackend(file.name, text);
  currentStats = backendResult ? statsFromBackend(backendResult.raw) : parseIfcText(text);
  updateStats(currentStats);
  drawPlaceholder(currentStats);

  if (backendResult) {
    statusBadge.textContent = "Backend connected";
    setBackendState(true);
    setText("analysisState", backendResult.message);
    activeMode = null;
    updateProcessedCounts(backendResult.processed);
    drawTopology(backendResult.processed);
  } else {
    setBackendState(false);
  }
});

resetView.addEventListener("click", () => {
  viewRotationX = -0.55;
  viewRotationZ = 0.72;
  viewZoom = 1;
  if (currentProcessed) drawTopology(currentProcessed);
});

modeScale.addEventListener("input", () => {
  stopSeismicAnimation();
  modeShapeScale = Number(modeScale.value);
  modeScaleValue.textContent = modeShapeScale.toFixed(2);
  if (currentProcessed) drawTopology(currentProcessed);
});

document.querySelectorAll('input[name="modeDisplay"]').forEach((input) => {
  input.addEventListener("change", () => {
    stopSeismicAnimation();
    modalDisplayMode = input.value;
    if (currentProcessed) drawTopology(currentProcessed);
  });
});

runGravity.addEventListener("click", () => {
  rerunWithCurrentGravity();
});

runSeismic.addEventListener("click", () => {
  rerunSeismic();
});

runXYComparison.addEventListener("click", () => {
  runXYComparisonAnalysis();
});

downloadSeismicReport.addEventListener("click", () => {
  downloadSeismicReportHtml();
});

runReliability.addEventListener("click", () => {
  runReliabilityAnalysis();
});

reliabilityGroundMotions.addEventListener("change", async (event) => {
  const files = [...event.target.files];
  reliabilityGroundMotionSet = [];
  for (const file of files) {
    reliabilityGroundMotionSet.push({
      filename: file.name,
      text: await file.text()
    });
  }
  reliabilityGroundMotionName.textContent = reliabilityGroundMotionSet.length
    ? `${reliabilityGroundMotionSet.length} records loaded`
    : "No records loaded";
  setText("reliabilityBatchSize", reliabilityGroundMotionSet.length ? `${reliabilityGroundMotionSet.length} records selected` : "-");
});

groundMotionFile.addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;

  groundMotionName.textContent = file.name;
  currentGroundMotionName = file.name;
  currentGroundMotionText = await file.text();
  statusBadge.textContent = "Ground motion loaded";
});

gravityDemandMetric.addEventListener("change", () => {
  stopSeismicAnimation();
  activeGravityDemandMetric = gravityDemandMetric.value;
  activeMode = null;
  if (currentProcessed) {
    renderGravity(currentProcessed.gravity);
    renderModalRows(currentProcessed.modal);
    drawTopology(currentProcessed);
  }
});

toggleAnimation.addEventListener("click", () => {
  activeMode = null;
  activeGravityDemandMetric = "none";
  gravityDemandMetric.value = "none";
  toggleSeismicAnimation();
  if (currentProcessed) drawTopology(currentProcessed);
});

animationTime.addEventListener("input", () => {
  stopSeismicAnimation();
  seismicAnimationFrameIndex = Number(animationTime.value || 0);
  updateAnimationTimeLabel();
  if (currentProcessed) drawTopology(currentProcessed);
});

animationScale.addEventListener("input", () => {
  seismicAnimationScale = Number(animationScale.value || 20);
  animationScaleValue.textContent = `${seismicAnimationScale.toFixed(0)}x`;
  if (currentProcessed) drawTopology(currentProcessed);
});

downloadTables.addEventListener("click", () => {
  if (!currentProcessed || !currentProcessed.tables) return;
  const payload = {
    counts: currentProcessed.counts,
    grid: currentProcessed.grid,
    tables: currentProcessed.tables,
    skipped: currentProcessed.skipped || null,
    model_options: currentProcessed.model_options || null,
    gravity: currentProcessed.gravity || null,
    modal: currentProcessed.modal || null,
    seismic: currentSeismic || null,
    reliability: currentReliability || null
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const name = projectTitle.textContent || "bim2struct";
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  link.href = url;
  link.download = `${name}_structural_tables_${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});

canvas.addEventListener("pointerdown", (event) => {
  isDragging = true;
  lastPointer = { x: event.clientX, y: event.clientY };
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", (event) => {
  if (!isDragging || !lastPointer || !currentProcessed) return;
  const dx = event.clientX - lastPointer.x;
  const dy = event.clientY - lastPointer.y;
  viewRotationZ += dx * 0.008;
  viewRotationX += dy * 0.008;
  viewRotationX = Math.max(-1.45, Math.min(0.25, viewRotationX));
  lastPointer = { x: event.clientX, y: event.clientY };
  drawTopology(currentProcessed);
});

canvas.addEventListener("pointerup", (event) => {
  isDragging = false;
  lastPointer = null;
  canvas.releasePointerCapture(event.pointerId);
});

canvas.addEventListener("wheel", (event) => {
  if (!currentProcessed) return;
  event.preventDefault();
  const delta = event.deltaY > 0 ? 0.9 : 1.1;
  viewZoom = Math.max(0.45, Math.min(3.2, viewZoom * delta));
  drawTopology(currentProcessed);
}, { passive: false });

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    document.querySelectorAll(".tab-page").forEach((page) => page.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(`${button.dataset.tab}Tab`).classList.add("active");
  });
});

drawEmpty();
checkBackendStatus();
