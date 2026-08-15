"""B3 -- prove a CUDA kernel actually launches on this GPU.

`torch.cuda.is_available()` returns True on a torch build that contains no
cubin for sm_121. You then discover the truth at the first real kernel launch,
usually hours later, inside someone else's library, as:

    RuntimeError: CUDA error: no kernel image is available for execution on the device

So this probe does arithmetic and checks the answer. It is the single most
load-bearing check in preflight, and it is four lines of real work.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import torch
    except Exception as exc:
        print(f"FAIL import torch: {exc}")
        return 1

    if not torch.cuda.is_available():
        print("FAIL torch.cuda.is_available() is False")
        return 1

    try:
        cap = torch.cuda.get_device_capability()
        name = torch.cuda.get_device_name(0)
        # Real work, real result. A build without an sm_121 cubin dies here.
        a = torch.randn(4096, 4096, device="cuda", dtype=torch.float32)
        b = torch.randn(4096, 4096, device="cuda", dtype=torch.float32)
        val = (a @ b).sum().item()
        torch.cuda.synchronize()
    except Exception as exc:
        print(f"FAIL kernel launch: {type(exc).__name__}: {exc}")
        return 1

    if val != val or val in (float("inf"), float("-inf")):
        print(f"FAIL matmul produced non-finite result: {val}")
        return 1

    arch_list = getattr(torch.cuda, "get_arch_list", lambda: ["?"])()
    print(f"OK sm_{cap[0]}{cap[1]} {name} matmul={val:.3e} archs={','.join(arch_list)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
