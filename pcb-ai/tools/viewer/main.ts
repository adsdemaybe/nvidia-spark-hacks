/**
 * The 3D half of the run viewer.
 *
 * Bundled by `tools/make-viewer.ts` and inlined into a single self-contained HTML file,
 * because the whole point is that the output is one file you can open, copy, or attach
 * — no dev server, no CDN, no network. Everything three.js needs is bundled; the GLB
 * arrives as base64 on `window.__GLB__`.
 */
import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js"

declare global {
  interface Window {
    __GLB__?: string
    __initViewer__?: () => void
  }
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

function init() {
  const mount = document.getElementById("three-root")
  const status = document.getElementById("three-status")
  if (!mount) return

  if (!window.__GLB__) {
    if (status) status.textContent = "No GLB was exported for this run."
    return
  }

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(mount.clientWidth, mount.clientHeight)
  mount.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(45, mount.clientWidth / mount.clientHeight, 0.1, 5000)

  // Light from several directions: a PCB is a flat thing seen edge-on half the time, and
  // a single light leaves the underside and the component sides unreadable.
  scene.add(new THREE.AmbientLight(0xffffff, 1.4))
  const key = new THREE.DirectionalLight(0xffffff, 2.0)
  key.position.set(60, 80, 60)
  scene.add(key)
  const fill = new THREE.DirectionalLight(0xffffff, 0.9)
  fill.position.set(-70, -40, 40)
  scene.add(fill)
  const rim = new THREE.DirectionalLight(0xffffff, 0.6)
  rim.position.set(0, 0, -80)
  scene.add(rim)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08

  const loader = new GLTFLoader()
  loader.parse(
    base64ToArrayBuffer(window.__GLB__),
    "",
    (gltf) => {
      scene.add(gltf.scene)

      // Frame the board from its actual bounds rather than a guessed camera distance —
      // boards range from a 20 mm blinker to a 100 mm controller.
      const box = new THREE.Box3().setFromObject(gltf.scene)
      const size = box.getSize(new THREE.Vector3())
      const centre = box.getCenter(new THREE.Vector3())
      const radius = Math.max(size.x, size.y, size.z) || 1
      const distance = radius * 1.9

      controls.target.copy(centre)
      camera.position.set(centre.x + distance * 0.7, centre.y - distance * 0.75, centre.z + distance * 0.8)
      camera.near = radius / 100
      camera.far = radius * 100
      camera.updateProjectionMatrix()
      controls.update()

      if (status) status.remove()
    },
    (err) => {
      if (status) status.textContent = `Could not load the 3D model: ${String(err)}`
    },
  )

  const resize = () => {
    if (!mount.clientWidth || !mount.clientHeight) return
    camera.aspect = mount.clientWidth / mount.clientHeight
    camera.updateProjectionMatrix()
    renderer.setSize(mount.clientWidth, mount.clientHeight)
  }
  window.addEventListener("resize", resize)
  // The 3D tab starts hidden, so the canvas is sized to a zero-height box on first
  // layout. Re-measuring when the tab becomes visible is what stops it rendering into
  // a 0px viewport.
  window.__initViewer__ = resize

  renderer.setAnimationLoop(() => {
    controls.update()
    renderer.render(scene, camera)
  })
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init)
} else {
  init()
}
