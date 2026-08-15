# Collision Geometry

## Why not a convex hull

PhysX and MuJoCo need convex shapes. The obvious move — convex-hull each visual
mesh — is wrong here: the chassis is a hollow tub, so its hull fills solid. The
electronics bay ceases to exist, the arm collides with a brick, and nothing
about the failure is obvious from the render.

## Why not a decomposed mesh either

VHACD-style decomposition works but is slower to compute, slower to simulate,
and produces shapes nobody can reason about when a contact goes wrong.

## Use primitives

This geometry is boxes and cylinders. Model it as boxes and cylinders:

- chassis — floor slab, four wall slabs, lid slab
- wheels — a cylinder about the drive axis
- turntable — a cylinder plus the two yoke prongs
- links — a body box plus a fork box
- jaws and gripper — boxes

It is exact for this design, fast, and stable in contact.

## Self-collision

Running clearances here are 0.4–1.0 mm. With self-collision enabled and no
filtering, adjacent links contact-jitter continuously. Emit adjacent-pair
`disable_collisions` entries — in the **SRDF**, since the element is not valid
URDF and is silently ignored there.

## Wheels need a material

Geometry alone gives no traction. Bind a physics material with static and
dynamic friction to the wheel colliders, or the robot slides frictionlessly and
the drive test fails for a reason that looks like a torque problem.
