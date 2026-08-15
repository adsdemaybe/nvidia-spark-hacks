# Master Example — "build me a hand that can pick up a cup"

The end-to-end demo this project exists to produce, written as one continuous story: what
the operator types, what each subsystem does with it, what proves it worked, and what is
still missing.

Everything runs on one NVIDIA DGX Spark (GB10, 121 GB unified memory, aarch64). Nothing
leaves the box.

> **The rule that governs every stage.** The agent proposes; the harness disposes. An
> agent may generate any design, critique or policy; it may never declare one valid. Only
> the deterministic gates return a verdict, and when a model and a measurement disagree,
> the measurement is right and the question is why the model was wrong.

---

## 0. The demo in one breath

1. Someone types **"a robot hand that can pick up a cup"** into the Studio UI.
2. **F1 (pcb-ai)** designs the boards the hand needs; **F2 (cad-generation)** designs the
   mechanism. They negotiate — each constrains the other — until the boards fit inside the
   hand and the hand can carry the boards.
3. **cosim** proves the electronics actually move the joints, and routes any failure back
   to whichever side owns it.
4. A teammate wears a headset and **picks up a cup**, hand tracked, in a scene where a
   virtual cup sits in front of the robot. Every episode is recorded.
5. Those episodes become a **LeRobot dataset**, replayed and randomised in **Isaac Lab** —
   cup pose, size, friction, lighting — and used to post-train **GR00T N1.7**.
6. In VR, the robot stands on a table with a cup in front of it. You say *"pick up the
   cup."* It does.

Every step of that is visible in one browser page while it happens.

---

## 1. What actually happens when you type the prompt

### Stage 1 — intent → two parallel design problems

The prompt goes to the Studio UI (`ui/studio.py`, port 8610). A planner splits it into the
two design problems that have to agree:

| | asks |
|---|---|
| **F2 — mechanism** | how many fingers, how many joints, what actuators, what reach and grip force |
| **F1 — electronics** | how many motor channels, what current, what sensing, what board area is available |

Neither can be answered alone. The number of actuators sets the number of driver channels;
the driver board's area and mass set what the palm can hold. That mutual dependence is the
feedback loop, and it is the interesting part of the demo.

### Stage 2 — the F1 ↔ F2 loop

```
        ┌────────────────────────── negotiation ──────────────────────────┐
        │                                                                 │
   F1 pcb-ai                                                       F2 cad-generation
   spec → parts plan → tscircuit HDL                        intent → Robot IR → build123d
   → L0–L10 gate ladder                                     → mass properties → evaluate()
        │                                                                 │
        ├── board_report ────────────────────────────────────────────────►│
        │   outline, thickness, mounting holes, component heightmap,       │
        │   connector edges, measured mass + CoM, thermal hotspots         │
        │                                                                 │
        │◄──────────────────────────────── envelope / BoardSpec ───────────┤
        │   max_outline the palm can accept, max component height,         │
        │   mount pattern, where connectors must face                      │
        │                                                                 │
        └─────────────────────────────────────────────────────────────────┘
```

**Where it converges.** F2 hands F1 an envelope ("the palm bay is 40 × 26 mm, components
under 6 mm, connectors must exit proximally"). F1 re-places and re-routes inside it
(`pcb.replace_within`) and returns a board report with *measured* facts. F2 fits the
enclosure or bay to the real outline and re-checks that the hand can still close.

**Where it bites, and why that is the point.** Running the two against each other finds
defects neither side can see alone. It already has:

- no rover board had **mounting holes**, so every enclosure generated zero standoffs — F1's
  gates do not care how a board is held, and F2 cannot invent a hole that is not in the
  report;
- `connector_edges` stored "position along the wall" while the CAD side read the pair as
  literal board coordinates, so every east/west **cutout landed outside the solid** — and
  `check_fit` compares edge, width and height but never position, so both sides agreed
  about a cutout that did not exist.

Two systems agreeing about something neither of them checks is the failure mode this loop
exists to catch.

### Stage 3 — proving the electronics move the mechanism

A hand that passes geometry checks may still be a hand that cannot grip. `cosim` couples
the two physically:

```
duty cycle ──► electrical model ──► torque ──► MuJoCo ──► joint angle ──┐
     ▲                                                                  │
     └────────────────── back-EMF, ω feeds back ───────────────────────┘
```

`cosim/tools/prove_drive.py` establishes the coupling is real rather than decorative, by
**ablation** rather than correlation. Series resistance is added between supply and motor —
what a real board contributes: copper, connector, H-bridge R_ds(on) — and the mechanics is
asked what changed:

```
 R series   peak I   |torque|    travel
  0.00 ohm  1.512 A  0.0083 N·m  5.249 rad
  4.00 ohm  0.252 A  0.0012 N·m  5.036 rad
```

Current −83 %, torque −85 %, travel −4 %, all monotonic. It is written so it can fail: a
coupling that does not respond, or responds in the wrong direction, prints NOT PROVEN.

For the hand demo this is the check that answers *"did the board you designed actually
close the fingers?"* — and the honest caveat today is that the travel signal is weak on a
free-spinning joint, because back-EMF dominates. **A grasp is a loaded, contact-rich task,
which is exactly where the ablation gets sharp**: too little current and the fingers stall
against the cup instead of merely turning slower.

### Stage 4 — failure routing

When a rollout fails, something has to decide whose problem it is, or the three-way loop
thrashes. The discriminator is a measurement, not a judgement:

- peak current **at the driver's limit** → the electronics cannot deliver more → **F1**
- current **well inside limits** and the joint is still slow → the mechanism needs more
  torque → **F2**
- the band between is reported as **ambiguous** rather than resolved by preference

Divergence routes to neither: a solver failure is not a design failure, and sending it to a
designer has them chase a fault that is not in their design.

---

## 2. Teaching it the task

Design produces a hand that *can* grip. It does not produce a hand that *knows how* to pick
up a cup. That comes from demonstration.

### 2.1 Capture — a person picks up a cup

A teammate wears a headset. In the scene, a virtual cup sits in front of the robot; they
reach out and pick it up with their own hand, tracked. This is the `arvr` subsystem
(currently on `feat/arvr-integration`, not on `main`), whose stated principle is that it is
*a spatial robotics interface, not a visualization*: what the hand does is retargeted onto
the robot and checked for reachability, not just drawn.

Its pipeline is already `normalize → retarget (Pinocchio) → verify (MuJoCo) → export`. For
this demo the export target is a **LeRobot dataset**, because that is the format the rest of
the chain speaks.

Per episode, recorded: hand/finger poses over time, the retargeted joint trajectory, the
cup's pose, contact events, and whether the grasp held.

### 2.2 The dataset

**LeRobot v3 dataset format**, on the shared Postgres + object store. Chosen because it is
what both NVIDIA's teleop tooling and GR00T's fine-tuning path already consume — inventing
a private format here would cost a converter at every downstream step and buy nothing.

Two sources feed one union:

- **human episodes** from the headset (few, high quality, real intent);
- **simulated episodes** from Isaac Lab (many, randomised, cheap).

### 2.3 Randomise, then post-train

Human demonstrations teach one cup in one place. The policy has to survive a cup that is
somewhere else, differently oriented, a different size, on a more slippery table.

**Isaac Lab** does the randomisation and the online post-training: cup pose and yaw, radius
and height, mass, friction, lighting and camera pose, table height. Its multi-environment
GPU stepping is what makes enough of that affordable on one box.

**GR00T N1.7** is the policy. It is an open vision-language-action model for manipulation,
fine-tunable on custom embodiments through the LeRobot format, which is exactly the shape
of this problem: our hand is a custom embodiment, our task is language-conditioned, and our
data is LeRobot episodes.

The training loop:

```
human episodes ──┐
                 ├──► LeRobot dataset ──► GR00T N1.7 fine-tune ──► policy
sim episodes ────┘                              ▲                     │
      ▲                                         │                     ▼
      └──── Isaac Lab randomised rollouts ──────┴──── evaluate: grasp success
                                                       across randomised cups
```

The gate is not loss. It is **grasp success rate over held-out randomised cup poses**, on
the real designed hand, in the simulator that already agrees with the co-simulation.

---

## 3. The payoff — talking to the robot in VR

Put the headset back on. The robot is on a table, a cup in front of it. You say:

> *"Pick up the cup."*

Speech → text → GR00T N1.7, conditioned on the scene camera and the instruction, emits
actions; the robot executes; you watch it happen from inside the scene, and you can move the
cup and ask again.

This is the demo, and it closes the circle: the hand doing the grasping was designed by the
pipeline in stage 1, its boards were verified to drive it in stage 3, and its policy was
learned from a human doing the same task in the same headset.

---

## 4. One page to watch it all

Everything above is visible in the browser while it runs.

| port | surface | what it shows |
|---|---|---|
| **8610** | **Studio** | the prompt box; per-iteration cards showing what the gates measured, what the reviewers want fixed, what the model changed, and whether it helped |
| 8600 | Console | PCB, CAD and joint viewers in one page |
| 8500 | PCB viewer | schematic, layout, DRC, per-board gate status |
| 3246 | CAD viewer | STEP/STL/3MF — the hand, the bays, the print plate |
| 8081 | Joint viewer | one slider per joint; drag it and the link moves |
| 8210 | CAD API | the F1↔F2 contract: `design_enclosure`, `check_fit`, `constrain_board` |
| 8220 | Docs RAG | tscircuit + build123d retrieval that grounds the design agents |
| 8100 | vLLM | the design model |
| 17670 | OpenShell gateway | sandboxed agent runs |

**Add for this demo:** a *training* pane (dataset size, episodes captured, randomisation
coverage, grasp success over time) and a *VR* pane (session state, what the robot is being
asked, what it did). Both belong beside Studio rather than in a separate tool — the point of
the console is that a robot is one thing.

---

## 5. Tech stack

| layer | choice | status |
|---|---|---|
| Design agents | **LangGraph** state machine per feature | **working** |
| Design model | **Qwen3-Coder-Next NVFP4** on vLLM (see §6) | **working**, 30.2 tok/s |
| Reviewer model | **Nemotron-3-Nano-Omni NVFP4**, vision-capable | served, not yet wired to reviewers |
| Grounding | **docs RAG** over tscircuit + build123d, BM25 | **working**, :8220 |
| PCB | **tscircuit** HDL → Circuit JSON → L0–L10 gates; KiCad 9 DRC as an independent second opinion; Freerouting as a routing cross-check | **working** |
| CAD | **build123d / OpenCascade**; `freeform` runs model-written build123d | **working** |
| Co-simulation | **MuJoCo** + ngspice-characterised drive surface | **working** |
| Robot format | **URDF / MJCF**, LeRobot v3 datasets | URDF/MJCF working |
| Teleop capture | **WebXR** hand tracking; **NVIDIA Isaac Teleop** for the demonstration-collection workflow | on a branch, not on `main` |
| Retarget / verify | **Pinocchio** (IK, retargeting) → **MuJoCo** (reachability) | on a branch |
| RL / randomisation | **NVIDIA Isaac Lab** — GPU-parallel envs, domain randomisation, online post-training | **not started** |
| Manipulation policy | **NVIDIA Isaac GR00T N1.7** — open VLA, fine-tunable on custom embodiments via LeRobot | **not started** |
| Sandboxed agent runs | **OpenShell / NemoClaw** — non-interactive agent turns, snapshots | **working**, :17670 |
| Fabrication | Gerbers + BOM (F1); STEP/STL/3MF, Bambu handoff (F2) | **working** |

---

## 6. The model question, answered honestly

The brief says *"using mainly Qwen3.8 27B."* **That model was measured on this box and
retired, and the reason matters for planning.**

Decode here is bound by **memory bandwidth, not compute**. Sampled mid-generation, the GPU
reported **96 % "utilization" while performing 0.6 % of its bf16 arithmetic** —
`utilization.gpu` counts time-with-a-kernel-resident, not capability used. What sets the
token rate is *bytes of weights read per token*:

| model | form | bytes/token | tok/s |
|---|---|---|---|
| Qwen3.8-27B | dense bf16 | ~45 GB | **3.8** |
| Qwen3-Coder-Next | NVFP4, 3B of 80B active | ~2 GB | **30.2** |

3.8 tok/s × 45 GB is 172 GB/s against the GB10's ~221 GB/s — about 78 % of peak. The dense
model was not misconfigured; it was the wrong *shape*. At ~4 tok/s a design loop cannot
afford the iterations that make it work at all.

**Recommendation:** keep **Qwen3-Coder-Next** for the design agents. If Qwen3.8-27B is
wanted for its own sake, it needs a 4-bit build — the same weights at ~11 GB/token would run
roughly 4× faster.

Note also that the design model and the robot policy are **different jobs**. Qwen writes
tscircuit and build123d; **GR00T** turns "pick up the cup" into joint actions. Neither
substitutes for the other.

---

## 7. What is built, and what this demo still needs

**Built and verified**

- F1 gate ladder end to end; three rover boards at 0 compile errors, every gate running
- F2 build123d generation, mass properties, URDF/MJCF export, `freeform` for
  model-written CAD, plus feature checks that catch a part that measures correctly and is
  still the wrong part
- F1 ↔ F2 negotiation: boards converge into enclosures, standoffs and cutouts, first attempt
- cosim: coupled rollout, gate, failure routing, and an ablation proving the drive is real
- Studio UI with a per-iteration play-by-play; docs RAG; sandboxed agent runs

**Not built — the honest gap list for this demo**

| gap | why it is not trivial |
|---|---|
| **A hand, not a rover** | every design so far is 3–9 links with one DoF per wheel. A hand is ~12–20 joints with coupled tendons and contact-rich grasping; `cosim` currently drives one joint |
| **Grasp-quality gates** | nothing yet measures whether a hand *can* grip: no force closure, no finger-workspace overlap, no payload-at-grip check |
| **Capture → dataset** | the arvr pipeline exports for verification, not as LeRobot episodes; the schema and store do not exist yet |
| **Isaac Lab** | not installed on this box; randomisation and online post-training are unstarted |
| **GR00T fine-tune** | unstarted; needs the dataset first, and a decision about whether the hand is close enough to a supported embodiment |
| **VR prompting** | the language → policy → robot path in-headset does not exist |
| **Memory contention** | vLLM already holds ~66–90 GB. GR00T training, Isaac Lab and a model server do not fit at once — this needs a schedule, not optimism |

**The one that will hurt most:** an AI-generated *board* is still not accepted by the gates.
Failures have moved from invented APIs to placement geometry, which is the tractable class,
but the demo's first stage depends on it. The rover boards that pass today were
human-seeded HDL, gated by the same ladder.

---

## 8. The order to build it in

1. **Grasp gates before grasp learning.** Add force closure, finger workspace and
   payload-at-grip to `evaluate()`. Without them the pipeline will happily converge on a
   hand that cannot hold anything, and every downstream hour is spent teaching a policy to
   use it.
2. **Make a hand converge.** Extend the generator vocabulary (a phalanx, a joint with a
   tendon route) and get F1↔F2 to close on it, with boards in the palm.
3. **Capture one episode, end to end.** One person, one cup, one LeRobot episode written and
   replayed. The format is the risk, not the volume.
4. **Isaac Lab, replay before randomise.** Reproduce the captured episode in sim first;
   randomisation on top of an unvalidated replay teaches noise.
5. **GR00T post-train, gated on grasp success** over held-out cup poses — not on loss.
6. **VR prompting last.** It is the thinnest layer and the most demo-visible; everything
   before it is what makes it work.

Each of those is a checkpoint with a measurement, which is the same contract the rest of
this project runs on: nothing is done because it looks done.
