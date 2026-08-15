# tools-cpu -- every stage that does not need CUDA.
#
# This is roughly half the pipeline: schemas, provenance, the artifact store,
# gate execution, shell fitting, cousin composition, task generation, USD
# authoring, convex decomposition, IK, and the MuJoCo physics validator.
#
# Why it is a separate image rather than a subset of recon-gpu:
#   * MuJoCo settle-tests run in seconds on CPU with no Kit boot and no RTX
#     context. Cousin rejection sampling runs them hundreds of times, so paying
#     Isaac's startup to ask "does this mug interpenetrate the table" is absurd.
#   * It keeps Pinocchio / CoACD / trimesh out of the 40GB CUDA image, where
#     they would fight NGC torch's numpy ABI for no benefit.
#   * It builds identically on arm64 anywhere, so the contract layer is testable
#     without the Spark.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      "pydantic>=2.7" "fsspec>=2024.6" "typer>=0.12" "rich>=13.7" "structlog>=24.1" \
      "numpy>=1.26" "scipy>=1.13" "trimesh>=4.4" "shapely>=2.0" "usd-core>=24.5" \
      "mujoco>=3.2" "coacd>=1.0.11" "rtree>=1.2" "pin>=2.7" "pyarrow>=16.0" "pillow>=10.3"

# python-fcl is trimesh's collision backend and a known ARM wheel gap. Install
# it opportunistically -- envgen falls back to trimesh broadphase + SAT, and
# preflight reports which path is live rather than failing the build here.
RUN pip install --no-cache-dir "python-fcl>=0.7" || \
    echo "WARN python-fcl unavailable on this arch; envgen will use the SAT fallback"

WORKDIR /repo
COPY packages /repo/packages
COPY pyproject.toml /repo/
RUN pip install --no-cache-dir --no-deps \
      -e packages/r2s-core -e packages/scan -e packages/envgen -e packages/r2s-cli

CMD ["bash"]
