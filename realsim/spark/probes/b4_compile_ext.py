"""B4 -- compile and run a trivial CUDA extension.

This is a ~3-minute proxy for a ~4-hour 3DGRUT build. Every heavy source build
in this project (3DGRUT, gsplat, Kaolin, any custom op) goes through exactly
this machinery, so if this fails they all will.

It sweeps ARCH_CANDIDATES and prints the first value that both compiles AND
produces a correct result. Record that string into env.json -- every downstream
build consumes it as TORCH_CUDA_ARCH_LIST.

Note the ordering: "12.0" first. sm_120 PTX JITs cleanly to sm_121 on GB10 and
is the reported-working value; PyTorch's arch-list parser may not accept "12.1"
at all even though CUDA 13's nvcc knows the target.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

SOURCE = r"""
#include <torch/extension.h>

__global__ void add_scaled(const float* a, const float* b, float* out, float k, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = a[i] + k * b[i];
}

torch::Tensor add_scaled_cuda(torch::Tensor a, torch::Tensor b, double k) {
    auto out = torch::empty_like(a);
    int n = a.numel();
    int threads = 256, blocks = (n + threads - 1) / threads;
    add_scaled<<<blocks, threads>>>(a.data_ptr<float>(), b.data_ptr<float>(),
                                    out.data_ptr<float>(), (float)k, n);
    return out;
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def("add_scaled", &add_scaled_cuda, "a + k*b (CUDA)");
}
"""


def try_arch(arch: str, workdir: str) -> tuple[bool, str]:
    os.environ["TORCH_CUDA_ARCH_LIST"] = arch
    import torch
    from torch.utils.cpp_extension import load

    build_dir = os.path.join(workdir, "b" + arch.replace(".", "").replace("+", "p").replace(";", "_"))
    os.makedirs(build_dir, exist_ok=True)
    src = os.path.join(build_dir, "probe.cu")
    with open(src, "w") as f:
        f.write(SOURCE)
    try:
        mod = load(
            name="r2s_probe_" + os.path.basename(build_dir),
            sources=[src],
            build_directory=build_dir,
            verbose=False,
        )
    except Exception as exc:
        return False, f"compile failed: {type(exc).__name__}: {str(exc)[-220:]}"

    try:
        a = torch.ones(1024, device="cuda")
        b = torch.full((1024,), 2.0, device="cuda")
        out = mod.add_scaled(a, b, 3.0)
        torch.cuda.synchronize()
    except Exception as exc:
        return False, f"ran but launch failed: {type(exc).__name__}: {str(exc)[-220:]}"

    expected = 1.0 + 3.0 * 2.0
    if not bool((out == expected).all().item()):
        return False, f"compiled and launched but produced wrong values (got {out[0].item()}, want {expected})"
    return True, "correct"


def main() -> int:
    candidates = os.environ.get("ARCH_CANDIDATES", "12.0 12.0+PTX 12.1 12.0;12.1").split()
    workdir = tempfile.mkdtemp(prefix="r2s_b4_")
    try:
        for arch in candidates:
            okay, detail = try_arch(arch, workdir)
            status = "ok " if okay else "no "
            print(f"  {status} TORCH_CUDA_ARCH_LIST={arch!r}: {detail}")
            if okay:
                print(f"OK arch={arch}")
                return 0
        print("FAIL no candidate arch list compiled+ran correctly")
        print("     do NOT start the 3DGRUT build until this passes")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
