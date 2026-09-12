"use client";

/**
 * The showroom.
 *
 * A lit room, a turntable, one car at a time, arrows to step through them. No
 * names and no captions: a showroom floor does not label its stock, and the
 * point of the section is that the object looks good, not that the visitor can
 * identify it.
 *
 * Two problems dominate the implementation.
 *
 * First, the files disagree about everything. Six artists exported these with
 * no shared convention for units or origin — one is in metres, one a hundred
 * times larger, one built around its rear axle. So nothing trusts the file:
 * each model is MEASURED after loading, scaled so its longest axis matches the
 * stage, and shifted so its underside rests on the platform. Per-model magic
 * numbers would be wrong the moment anyone swapped a file.
 *
 * Second, only two of the six carry animation. The Lamborghini and the Mustang
 * have one clip each and it plays automatically; the rest have none, so there
 * is nothing to play and no amount of code will invent it.
 */

import {
  ContactShadows,
  Environment,
  OrbitControls,
  useAnimations,
  useGLTF,
} from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { AnimatePresence, motion } from "motion/react";
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import * as THREE from "three";

interface Car {
  file: string;
  credit: string;
  /** Vertical framing nudge for cars with unusual proportions. */
  aim?: number;
}

export const CARS: Car[] = [
  { file: "/models/mustang.glb", credit: "Ford Mustang 1965" },
  { file: "/models/lamborghini.glb", credit: "Lamborghini Centenario LP-770 · SDC" },
  { file: "/models/supra.glb", credit: "Toyota Supra" },
  { file: "/models/bmw.glb", credit: "BMW M3 E30" },
  { file: "/models/mclaren.glb", credit: "McLaren 765LT" },
  { file: "/models/porsche.glb", credit: "Porsche 911 (930) Turbo · Troublesome. · Sketchfab" },
];

/**
 * How wide a car is drawn, in scene units.
 *
 * This is the single number controlling how big the cars look, because every
 * model is normalised to it. Raising it and pulling the camera in together is
 * what stops the stage reading as an object sitting in a box: the car should
 * fill the frame the way it would fill your view standing next to it.
 */
const STAGE_WIDTH = 6.6;

function FittedCar({ file }: { file: string }) {
  const { scene, animations } = useGLTF(file);
  const group = useRef<THREE.Group>(null);
  const { actions } = useAnimations(animations, group);

  useLayoutEffect(() => {
    const g = group.current;
    if (!g) return;
    g.scale.setScalar(1);
    g.position.set(0, 0, 0);

    const box = new THREE.Box3().setFromObject(scene);
    const size = new THREE.Vector3();
    const centre = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(centre);

    // Fit the LONGER horizontal axis. The diagonal would shrink long cars;
    // width alone would let one overhang the platform.
    const span = Math.max(size.x, size.z) || 1;
    const scale = STAGE_WIDTH / span;
    g.scale.setScalar(scale);
    g.position.set(-centre.x * scale, -box.min.y * scale, -centre.z * scale);

    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) m.castShadow = true;
    });
  }, [scene, file]);

  // Play whatever the file carries. Two of the six have a clip; the others
  // simply have nothing to start, which is not an error.
  useEffect(() => {
    const names = Object.keys(actions);
    if (!names.length) return;
    const a = actions[names[0]];
    a?.reset().setLoop(THREE.LoopRepeat, Infinity).play();
    return () => {
      a?.stop();
    };
  }, [actions]);

  return (
    <group ref={group}>
      <primitive object={scene} />
    </group>
  );
}

/** The room: walls and floor, so the car sits somewhere rather than nowhere. */
function Room() {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -2.1, 0]} receiveShadow>
        <circleGeometry args={[34, 72]} />
        {/* Near-black and quite glossy. A dark floor does two things a pale one
            cannot: it separates a dark car from its background by reflection
            rather than by outline, and it stops the room competing with the
            only lit object in it. */}
        <meshStandardMaterial color="#0d1015" metalness={0.75} roughness={0.24} />
      </mesh>
      {/* A faint halo on the floor, as in the reference: it reads as light
          spilling off the stage rather than as a drawn circle. */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -2.08, 0]}>
        <ringGeometry args={[6.6, 7.3, 110]} />
        <meshBasicMaterial color="#7fb2ff" transparent opacity={0.32}
                           side={THREE.DoubleSide} />
      </mesh>
      {[
        [0, -17] as const,
        [-17, 0] as const,
        [17, 0] as const,
      ].map(([x, z], i) => (
        <mesh key={i} position={[x, 7, z]}
              rotation={[0, i === 0 ? 0 : Math.PI / 2, 0]}>
          <planeGeometry args={[42, 24]} />
          <meshStandardMaterial color="#171b22" roughness={1} metalness={0} />
        </mesh>
      ))}
    </group>
  );
}

function Platform() {
  return (
    <group position={[0, -0.13, 0]}>
      <mesh receiveShadow>
        <cylinderGeometry args={[4.6, 4.6, 0.3, 88]} />
        <meshStandardMaterial color="#1c2129" metalness={0.8} roughness={0.18} />
      </mesh>
      <mesh position={[0, 0.142, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[4.36, 4.62, 88]} />
        <meshStandardMaterial color="#ffffff" emissive="#dfeaff"
                              emissiveIntensity={1.8} side={THREE.DoubleSide} />
      </mesh>
      {/* Scissor lift */}
      {[-1, 1].map((s) => (
        <mesh key={s} position={[0, -0.78, 0]} rotation={[0, 0, (s * Math.PI) / 5]}>
          <boxGeometry args={[3.7, 0.16, 0.16]} />
          <meshStandardMaterial color="#1b2029" metalness={0.8} roughness={0.3} />
        </mesh>
      ))}
      <mesh position={[0, -1.42, 0]}>
        <boxGeometry args={[3.3, 0.18, 2.1]} />
        <meshStandardMaterial color="#171b22" metalness={0.6} roughness={0.5} />
      </mesh>
    </group>
  );
}

function Turntable({ file, dragging }: { file: string; dragging: boolean }) {
  const ref = useRef<THREE.Group>(null);
  useFrame((_, delta) => {
    if (!dragging && ref.current) ref.current.rotation.y += delta * 0.2;
  });
  return (
    <group ref={ref}>
      <Suspense fallback={null}>
        <FittedCar file={file} />
      </Suspense>
      <Platform />
    </group>
  );
}

function Stage({ file }: { file: string }) {
  const [dragging, setDragging] = useState(false);
  return (
    <Canvas
      shadows
      dpr={[1, 2]}
      camera={{ position: [7.4, 3.0, 7.4], fov: 32 }}
      style={{ touchAction: "none" }}
      gl={{ toneMapping: THREE.ACESFilmicToneMapping, antialias: true }}
    >
      <color attach="background" args={["#0f1217"]} />
      <fog attach="fog" args={["#0f1217", 22, 50]} />

      {/* Track lighting, as in the reference: a row of spots raking down a
          white room. One hard key would flatten a car body to a silhouette. */}
      {/* Low ambient. In a dark room the spots have to do the work, which is
          what gives a car body its highlights instead of a flat wash. */}
      <ambientLight intensity={0.28} />
      {[-7, -3.5, 0, 3.5, 7].map((x, i) => (
        <spotLight
          key={x}
          position={[x, 9.5, 5.5]}
          angle={0.36}
          penumbra={0.95}
          intensity={i === 2 ? 5.2 : 3.0}
          color={i === 2 ? "#ffffff" : "#dbe7ff"}
          castShadow={i === 2}
          shadow-mapSize={[2048, 2048]}
          shadow-bias={-0.0004}
        />
      ))}
      {/* Rim lights from behind, which is what separates a dark car from a
          dark room. Without them the silhouette merges into the background. */}
      <spotLight position={[-9, 5, -7]} angle={0.7} penumbra={1} intensity={3.4}
                 color="#9fc4ff" />
      <spotLight position={[9, 5, -7]} angle={0.7} penumbra={1} intensity={3.0}
                 color="#ffd9b0" />
      <Environment preset="warehouse" />

      <Room />
      <Turntable file={file} dragging={dragging} />

      <ContactShadows position={[0, -0.3, 0]} opacity={0.8} scale={22}
                      blur={2.4} far={7} color="#000000" />
      <OrbitControls
        enablePan={false}
        enableZoom={false}
        target={[0, 0.75, 0]}
        minPolarAngle={Math.PI / 4.8}
        maxPolarAngle={Math.PI / 2.12}
        onStart={() => setDragging(true)}
        onEnd={() => setDragging(false)}
        rotateSpeed={0.8}
      />
    </Canvas>
  );
}

export function CarShowcase() {
  const [i, setI] = useState(0);
  const car = CARS[i];
  const go = (d: number) => setI((n) => (n + d + CARS.length) % CARS.length);

  return (
    <div className="relative">
      <div className="relative h-[clamp(460px,76vh,880px)] w-full overflow-hidden rounded-xl"
        style={{ background: "#0f1217" }}>
        <Stage key={car.file} file={car.file} />

        {/* Arrows sit ON the stage, vertically centred, where a showroom would
            put them. Below the frame they would read as a caption. */}
        <Arrow side="left" onClick={() => go(-1)} />
        <Arrow side="right" onClick={() => go(1)} />

        <div className="pointer-events-none absolute bottom-4 left-0 right-0 flex flex-col items-center gap-2">
          <div className="flex gap-[6px]">
            {CARS.map((c, n) => (
              <span
                key={c.file}
                className="h-[5px] rounded-full transition-all duration-300"
                style={{
                  width: n === i ? 22 : 5,
                  background: n === i ? "#ffffff" : "rgba(255,255,255,0.3)",
                }}
              />
            ))}
          </div>
          <span className="legend" style={{ color: "rgba(255,255,255,0.5)" }}>
            drag to turn
          </span>
        </div>
      </div>

      {/* Attribution is a licence condition, not decoration — but it belongs
          quiet and small, not competing with the car. */}
      <div className="mt-2 h-4 text-center">
        <AnimatePresence mode="wait">
          <motion.span
            key={car.file}
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.55 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="font-mono text-[10px] text-ink-mute"
          >
            {car.credit}
          </motion.span>
        </AnimatePresence>
      </div>
    </div>
  );
}

function Arrow({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  return (
    <motion.button
      onClick={onClick}
      whileHover={{ scale: 1.07 }}
      whileTap={{ scale: 0.94 }}
      aria-label={side === "left" ? "Previous car" : "Next car"}
      className="absolute top-1/2 z-10 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-rule backdrop-blur"
      style={{
        [side]: "1rem",
        background: "rgba(255,255,255,0.10)",
        borderColor: "rgba(255,255,255,0.22)",
        color: "#ffffff",
      }}
    >
      <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden>
        <path
          d={side === "left" ? "M10 2 L4 8 L10 14" : "M6 2 L12 8 L6 14"}
          stroke="currentColor"
          strokeWidth="1.9"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </motion.button>
  );
}
