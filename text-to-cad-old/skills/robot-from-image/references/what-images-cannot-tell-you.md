# What an Image Cannot Tell You

## No scale, ever

A render of a 200 mm rover and a 2 m rover are the same pixels. Nothing in an
image recovers absolute size without a known reference object in frame. Every
dimension you produce from an image is a ratio.

This is why `scripts/silhouette` uses an **orthographic** camera and normalises
both masks to their bounding box: it deliberately discards scale so that the
comparison measures only what the image actually carries.

If an absolute size is needed, it is an assumption *you* introduce. Put it in
`absolute_hint_mm`, and say in your report that you introduced it.

## No mechanism

A silhouette cannot distinguish:

- a gripper that opens from one moulded to look like it opens
- a turntable that rotates from a cylinder
- a driven wheel from a caster
- a real bearing from a printed hole

This project already shipped a gripper with a 14 mm aperture and no enforced
jaw coupling. It would photograph perfectly.

## No mass, material, or manufacturability

Nothing about density, wall thickness, print orientation, fastener access, or
whether the part can be assembled in that order.

## What it CAN tell you, reliably

- **Topology** — wheel count, number of arm segments, roughly where the arm
  mounts, whether the chassis is a box or a frame
- **Proportion** — to about two significant figures
- **Silhouette** — the outline, which is a genuine constraint and worth checking
- **Gross configuration** — differential drive vs ackermann, arm forward vs
  centre-mounted

## Generated images specifically

An AI-generated concept image is not an engineering drawing. It may contain
impossible geometry, inconsistent part counts between views, and details that
exist only because they looked plausible. Treat every reading as a hypothesis to
be tested by `scripts/fit`, whose conflict list is where the impossibilities
surface.
