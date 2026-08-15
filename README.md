# nvidia-spark-hacks

STRUCT prototypes targeting the NVIDIA DGX Spark (GB10 Grace Blackwell,
sm_121, aarch64, CUDA 13).

| dir | what |
|---|---|
| [`realsim/`](realsim/) | F3: one phone video -> 8 validated digital-cousin simulation scenes. Start at [`realsim/STATE.md`](realsim/STATE.md). |
| [`arvr/`](arvr/) | F4+F5: AR/XR spatial robotics interface — PLACE, TEACH, REPLAY, FOLLOW, TEST, CORRECT, TWIN. Spec: [`ar-xr-plan.md`](ar-xr-plan.md). Start at [`arvr/STATE.md`](arvr/STATE.md). |

Other planning docs: [`STRUCT_2.md`](STRUCT_2.md) (whole-project master plan,
all 5 feats), [`ar-xr-plan.md`](ar-xr-plan.md) (F4+F5 detailed spec —
`arvr/`'s code comments cite this as "spec section N"), [`text-to-cad-plan.md`](text-to-cad-plan.md),
[`text-to-pcb-plan.md`](text-to-pcb-plan.md).
