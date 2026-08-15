# Inertials

## The reference-frame trap

OpenCascade's `GProp_GProps.MatrixOfInertia()` is referenced to the **centre of
mass**, not the origin. Treating it as origin-referenced and then applying a
parallel-axis shift subtracts the offset twice and yields tensors with negative
eigenvalues — physically impossible, and rejected by every physics engine.

Verified empirically: a 100 mm cube centred at (200, 0, 0) returns
`V(a²+b²)/12`, the COM-referenced value, not the much larger origin-referenced
one. When in doubt, run that test again rather than reasoning about it.

## Composition

Accumulate everything about the link origin, then shift once to the combined
centre of mass, which is what both URDF and USD expect:

    I_origin  = Σ [ I_body_i + m_i (|r_i|² E − r_i r_iᵀ) ]
    com       = Σ m_i r_i / Σ m_i
    I_com     = I_origin − M (|com|² E − com comᵀ)

## Point masses are not a shortcut

A dimensionless point mass has a rank-deficient tensor. Summing several produces
a combined tensor that violates the triangle inequality `A + B >= C`. Give every
lumped component a real bounding box and a solid-box tensor about its own
centre:

    I_body = (m/12) · diag(y²+z², x²+z², x²+y²)

## Validity check

Every tensor must satisfy, after diagonalisation:

- all eigenvalues strictly positive
- `A + B >= C` for the sorted eigenvalues

USD additionally wants a diagonalised tensor plus a principal-axes quaternion,
so eigendecompose and ensure the eigenvector matrix is right-handed
(`det > 0`) before converting to a quaternion.
