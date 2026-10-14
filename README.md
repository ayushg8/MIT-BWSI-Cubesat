<p align="center"><img src="docs/media/banner.png" alt="MuraltZ CubeSat flight software" width="100%"></p>

<p align="center">
<a href="https://github.com/ayushg8/MIT-BWSI-Cubesat/actions/workflows/tests.yml"><img src="https://github.com/ayushg8/MIT-BWSI-Cubesat/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
<img src="https://img.shields.io/badge/python-3-3776AB?logo=python&logoColor=white" alt="Python 3">
<img src="https://img.shields.io/badge/Raspberry%20Pi-4-C51A4A?logo=raspberrypi&logoColor=white" alt="Raspberry Pi 4">
</p>

# MuraltZ CubeSat: flight software

Raspberry Pi flight software for the MuraltZ CubeSat prototype, built at the MIT Beaver Works Summer Institute. The loop points the camera, shoots only when the IMU says it is steady and facing the ground, scores each photo on board, and sends the best ones home within a fixed byte budget. The companion ground station is in [MIT-Cubesat-Ground-Control-Station](https://github.com/ayushg8/MIT-Cubesat-Ground-Control-Station).

**[Open the interactive site](https://ayushg8.github.io/MIT-BWSI-Cubesat/)** to click through the logic yourself.

### What it does

- It only takes a photo when the camera faces the ground and is holding still.
- Every shot is checked on board for blur, brightness and shake, and bad ones are thrown away.
- The best photos go first, and the rest wait for the next pass.

<p align="center"><img src="docs/media/demo.gif" alt="Recording of the interactive site: the hero, an accepted and a rejected capture with per-check results, and the downlink queue draining" width="880"></p>

*A recording of the [interactive site](https://ayushg8.github.io/MIT-BWSI-Cubesat/), which replays the flight logic with the same thresholds as `config.py`. It is not hardware footage.*

## How it works

<p align="center"><img src="docs/media/figures/state-machine.svg" alt="Animated diagram: the active state steps through BOOT, WAITING, IMAGING, IDLE and DOWNLINK, then returns to WAITING" width="880"></p>

The flight loop is a state machine stepped by an operator (`cubesat_flight/main.py`). After boot it waits for a pass command, then runs IMAGING, IDLE and DOWNLINK and goes back to waiting. Fig. 1 is an animation, so the active state moves by itself.

<details>
<summary>What each state does</summary>

**BOOT** checks the IMU, takes a test photo that must decode as a JPEG, checks disk space and tries to reach the ground station. If the ground station is missing, the spacecraft logs autonomous mode and carries on.

**IMAGING** (`states/imaging.py`) reads the IMU every 3 seconds and shoots only when the camera is near nadir and the gyro reads under 1.0 rad/s. Each photo goes through the quality gate, and a photo that survives gets a priority tier from how new its spot on an 8 x 8 coverage grid is.

**IDLE** (`states/idle.py`) builds the send queue sorted by tier and score. Priority runs P1, P2, P3 from most to least important, and a P2 photo that has waited more than 2 passes drops to P3. IDLE also deletes the oldest P3 photos when storage fills up and applies any commands from the ground.

**DOWNLINK** (`states/downlink.py`, `comms/transfer.py`) sends telemetry, then photos highest priority first, until the byte budget or the time window runs out.

Three background pieces keep it alive. A watchdog thread restarts the process if the main loop stalls for 30 seconds. Thermal monitoring doubles the capture interval at 70 C and drops to safe mode at 80 C. A command listener takes validated JSON commands from the ground station over TCP. More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

</details>

## Pointing: nadir lock

<p align="center"><img src="docs/media/figures/fig-nadir-hysteresis.png" alt="Fig. 2: lock state against angle from nadir; descending the lock engages at 44 degrees, returning it holds until 56" width="880"></p>

Nadir is straight down at the ground. The camera locks when it points within 45 degrees of nadir and lets go only past 55, so a camera hovering near 50 does not flicker on and off. Fig. 2 feeds a 1 degree sweep through the real `_update_nadir_lock`.

## Quality gate on real captures

<p align="center"><img src="docs/media/figures/fig-contact-sheet.png" alt="Fig. 3: contact sheet of all 16 captures, each stamped ACCEPT or REJECT with its blur variance" width="880"></p>

Each photo is scored on blur, brightness and shake. The blur score is the Laplacian variance, which gets bigger the sharper the edges are, and the gate wants at least 20. I shrank the 16 photos in `Images/` to the flight camera's 640 x 480, saved them the way the camera code does, and ran the repo's own `QualityGate.score_image`. 10 of 16 pass. The 6 rejects are all logged at the blur check, which runs first; 3 of them are near-black frames (mean brightness under 7) that would also fail the exposure minimum of 15.

<p align="center"><img src="docs/media/figures/fig-gate-variance.png" alt="Fig. 4: blur variance of each capture at 640 x 480 and at full resolution against the threshold of 20" width="880"></p>

The faint diamonds in Fig. 4 are the same photos at the full 4608 x 2592. Scored there, the gate passes only 1 of 16, because variance shifts with resolution. That is why 20 is still a placeholder. The [site](https://ayushg8.github.io/MIT-BWSI-Cubesat/#gate) shows the per-check breakdown for each photo.

<p align="center"><img src="docs/media/figures/terminal-gate-and-tests.gif" alt="Terminal recording: a script prints ACCEPT or REJECT for each of the 16 captures, then the processing unit tests run and pass" width="880"></p>

The terminal output above is a real run of `docs/figures/gate_report.py` and `python -m unittest -v test_processing.py`, replayed as frames.

<details>
<summary>How the gate decides</summary>

```mermaid
%%{init: {'theme': 'neutral'}}%%
flowchart TD
    A[JPEG] --> B{readable?}
    B -- no --> R[reject]
    B -- yes --> C{blur variance >= 20?}
    C -- no --> R
    C -- yes --> D{mean brightness 15 to 240?}
    D -- no --> R
    D -- yes --> E{gyro rate < 1.0 rad/s?}
    E -- no --> R
    E -- yes --> F["score = 0.3 blur + 0.25 exposure + 0.2 motion + 0.25 novelty"]
```

Gyro was set to 0.0 and novelty to 1.0 for these runs, since no IMU or coverage grid was attached. Per-image numbers at both resolutions are in [`docs/data/captures.json`](docs/data/captures.json).

</details>

## Downlink

<p align="center"><img src="docs/media/figures/fig-downlink-timeline.png" alt="Fig. 5: cumulative bytes delivered over one 60 second window; two photos fit, a third would pass the 72,000 byte budget" width="880"></p>

The link moves 1,200 bytes per second for a 60 second window, so a pass carries 72,000 bytes at most. Fig. 5 runs the real `downlink()` loop against a stub socket on a virtual clock, with the accepted captures queued best score first. Two photos go out, and the third would cross the budget, so it waits for the next pass.

```mermaid
%%{init: {'theme': 'neutral'}}%%
sequenceDiagram
    participant S as CubeSat (transfer.py)
    participant G as Ground station
    S->>G: JSON header: type, filename, file_size, md5, metadata
    loop 1,200 byte chunks
        S->>G: chunk
        Note over S: sleep 1 s
    end
    alt checksum matches
        G-->>S: ACK
        Note over S: mark sent
    else checksum fails
        G-->>S: NACK
        Note over S: retry, up to 3 NACKs, then mark corrupt and skip
    end
```

## Offline analysis layer

<p align="center"><img src="docs/media/figures/fig-offline-analysis.png" alt="Fig. 6: a synthetic scene, its pixel segmentation, and an A* route through the resulting cost grid" width="880"></p>

Terrain segmentation, the cost grid and A* route planning live in `processing/` and run in the tests, but the flight loop does not call them. Fig. 6 uses a synthetic scene because the indoor captures have no terrain to segment; the detection boxes are placed by hand.

## Tech

- Python 3 on a Raspberry Pi 4
- LSM6DSO32 IMU over I2C (Adafruit CircuitPython), accelerometer and gyro
- Pi Camera Module 3 through `picamera2`
- OpenCV and NumPy for the quality gate and offline analysis, Flask for the inspection dashboard

<details>
<summary>Repo layout</summary>

```
.
├── cubesat_flight/
│   ├── main.py            state machine loop
│   ├── config.py          every mission parameter
│   ├── protocol.py        contract shared with the ground station
│   ├── states/            boot, imaging, idle, downlink, safe_mode
│   ├── sensors/           IMU and camera wrappers
│   ├── processing/        quality gate, coverage grid, offline analysis
│   ├── comms/             throttled transfer, packets, command listener
│   ├── storage/           queue, image index, aging, cleanup
│   ├── utils/             watchdog, thermal, telemetry, logger
│   ├── dashboard/         offline Flask inspection app
│   └── test_*.py          processing tests and a hardware self-check
├── docs/
│   ├── index.html         interactive site (GitHub Pages)
│   ├── ARCHITECTURE.md    full design write-up
│   ├── data/captures.json quality-gate results for Images/
│   ├── figures/           scripts that generate every figure above
│   └── media/             banner, demo GIF, figures, capture thumbnails
├── Images/                16 raw test captures
└── .github/workflows/     CI
```

</details>

## Running it

On the Pi:

```bash
sudo apt install -y python3-picamera2 python3-opencv
pip install -r requirements-hardware.txt
cd cubesat_flight
python test_hardware.py     # IMU / camera / thermal / storage / link self-check
python main.py              # then type: start_pass, cell R C, end_pass, status, shutdown
```

On a laptop, where only the offline analysis tests run:

```bash
pip install -r requirements.txt
cd cubesat_flight
export CUBESAT_DATA_ROOT="$(mktemp -d)"
python -m unittest -v test_processing.py
```

To rebuild the figures (needs Python 3.11 or newer and `pip install matplotlib`):

```bash
python docs/figures/make_figures.py
```

`GROUND_STATION_IP` and the data root can be overridden through environment variables (see `.env.example`).

## Limits

This is an educational prototype, and the numbers here are designed behavior from `config.py`, not flight results.

- The blur threshold of 20 is a placeholder that has not been calibrated on the real imaging surface.
- There is no magnetometer, so pitch and roll are tracked but yaw is not.
- Wi-Fi stands in for a flight radio. The 1,200 byte/s throttle matches a 9600 baud UHF link.
- Camera height and power-draw values in `config.py` are still 0.0 placeholders.

| Setting | Value |
| --- | --- |
| Nadir lock / release | 45 / 55 degrees |
| Gyro stability gate | under 1.0 rad/s |
| Exposure gate (mean pixel) | 15 to 240 |
| Link rate | 1,200 bytes/s |
| Per-pass budget | 72,000 bytes in 60 s |
| Thermal throttle / safe mode | 70 / 80 C |
