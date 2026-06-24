# BIM2Struct Web Prototype

This repository contains a local web prototype for the BIM2Struct workflow, which is for demonstration/review purposes only. Modifications or redistribution without permission are strictly prohibited.

The current version supports:

- loading an IFC file in the browser;
- showing raw IFC statistics;
- sending the IFC file to a local Python backend;
- running the BIM2Struct IFC-to-table processor;
- returning processed node/member counts;
- returning lightweight node and element tables for topology visualization;
- showing a rotatable 3D structural topology preview;
- running OpenSees gravity analysis with editable dead load, live load, and mass participation factor;
- visualizing gravity member demand by axial force, bending moment, or shear force;
- selecting gravity load cases/combinations and visualizing preliminary demand-capacity ratio;
- enabling or disabling X-direction expansion-joint boundary springs with editable stiffness;
- running OpenSees modal analysis from the backend;
- showing modal frequencies, periods, dominant directions, and clickable mode-shape views;
- adjusting mode-shape scale and switching between undeformed, deformed, and overlay views;
- running nonlinear OpenSees seismic response analysis from uploaded ground-motion records;
- scaling ground motions automatically to a target PGA in `g`;
- using an adaptive seismic solver with optional smaller analysis time step, fallback algorithms, and step subdivision;
- showing seismic response summaries, roof displacement histories, maximum IDR histories, per-story IDR histories, peak IDR profiles, and peak displacement profiles;
- animating the seismic response in the 3D topology preview from diaphragm master displacements;
- running seismic reliability/fragility batch workflow over multiple ground motions and PGA levels;
- estimating empirical exceedance probabilities and reliability indices for user-defined IDR limits;
- downloading enriched structural node/member tables, gravity results, modal results, and seismic results as JSON;
- downloading a standalone seismic report as HTML.


## Python Environment

The backend should be run with the local conda environment, This environment must include:

- IfcOpenShell
- OpenSeesPy
- NumPy

You can check the environment with:

```powershell
run -n BIMFEMenv python -c "import ifcopenshell; import ifcopenshell.geom; import openseespy.opensees as ops; print('OK')"
```

## Run the Backend

From this folder, run:

```powershell
run_backend.bat
```

Keep this terminal window open while using the web page.

## Run the Web Page

Open this file in a browser:

```text
web/index.html
```
If the backend is not running, the page still shows raw IFC statistics, but BIM2Struct processed results remain `Backend pending`.

## Seismic Response Workflow

The Seismic tab runs nonlinear OpenSees time-history analysis on the analysis model generated from the IFC structural tables. The ground-motion input should be a text file with one acceleration value per line. The current web workflow assumes the record values are in either `g` or `mm/s2`.

When `Acceleration unit = g`, the backend reads the peak absolute value of the uploaded record and automatically scales it to the requested target PGA:

```text
record_scale = target_pga_g / max(abs(record_values))
OpenSees acceleration = record_values * record_scale * 9810 mm/s2
```

For example, if the uploaded record has a peak value of `0.50 g` and `Target PGA (g) = 0.20`, the record scale is:

```text
0.20 / 0.50 = 0.40
```

The seismic solver supports:

- `Ground motion dt (s)`: the sampling interval of the uploaded ground-motion file;
- `Analysis dt (s)`: an optional smaller integration step for the OpenSees transient analysis;
- `Failure step subdivisions`: the maximum number of times a failed step can be split into smaller substeps;
- fallback solution strategies including Newton, Newton line search, Modified Newton, Krylov Newton, `NormDispIncr`, and `EnergyIncr` tests.

The Seismic tab reports:

- peak roof displacement in `mm`;
- peak inter-storey drift ratio in `%`;
- critical story;
- collapse check based on the user-defined IDR threshold;
- solver diagnostics, including failed time and failed element when available;
- roof displacement time history;
- maximum IDR time history;
- per-story IDR histories using different colors;
- peak IDR profile by story;
- peak displacement profile by story.


## Seismic HTML Report

The `Download seismic report HTML` button exports a standalone HTML report containing:

- model and processed member counts;
- ground-motion and seismic analysis parameters;
- peak response summary;
- modal summary;
- optional independent X/Y comparison results;
- embedded SVG charts for roof displacement, maximum IDR, per-story IDR histories, peak IDR profile, and peak displacement profile.

The report is generated entirely in the browser from the current displayed results. Re-run the seismic analysis or independent X/Y comparison before exporting if parameters or chart data have changed.

## Seismic Reliability / Fragility

The Reliability tab provides an initial batch workflow for seismic reliability and fragility assessment. It reuses the existing nonlinear seismic API and runs independent analyses over:

- multiple uploaded ground-motion records;
- user-defined target PGA levels, for example `0.10, 0.20, 0.30, 0.40, 0.50 g`;
- user-defined IDR limits, for example `1%, 2%, 4%`.

For each PGA level and each IDR limit, the web page computes the empirical exceedance probability:

```text
Pf(IM, IDR_limit) = N(peak_IDR > IDR_limit) / N(total completed records)
```


