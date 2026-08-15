# sim -- Isaac Sim plus our validation entrypoints, nothing else.
#
# The base image is multi-arch and runs on aarch64/DGX Spark. Known aarch64
# gaps: no live streaming, no cuRobo/SkillGen. We only need headless offline
# render, which is unaffected.
#
# Isaac's bundled Kit Python is a CLOSED RUNTIME. Do not pip install into it --
# that is why this is a separate image rather than a layer on recon-gpu. Our
# code reaches it only as scripts executed by ${ISAACSIM_PATH}/python.sh, and
# talks to the rest of the pipeline through the shared /work mount and JSON on
# stdout.
ARG ISAAC_TAG=5.1.0
FROM nvcr.io/nvidia/isaac-sim:${ISAAC_TAG}

# Documented aarch64 OpenMP workaround; without it Kit's python fails to import.
ENV LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1
ENV ACCEPT_EULA=Y PRIVACY_CONSENT=Y

COPY spark/probes /probes
COPY packages/envgen/src/envgen/validate /isaac-entry

CMD ["bash"]
