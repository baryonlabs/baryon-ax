import * as THREE from "./vendor/three.module.min.js";

const canvas = document.getElementById("hero-canvas");
const hero = document.querySelector(".hero");
if (canvas && hero) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0, 8);
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);

  const group = new THREE.Group();
  scene.add(group);

  let seed = 20261113;
  const random = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };

  const isCompact = window.innerWidth < 760;
  const count = isCompact ? 34 : 68;
  const points = [];
  for (let index = 0; index < count; index += 1) {
    const radius = 5.4 * Math.cbrt(random());
    const theta = random() * Math.PI * 2;
    const phi = Math.acos(2 * random() - 1);
    points.push(new THREE.Vector3(
      radius * Math.sin(phi) * Math.cos(theta),
      radius * Math.sin(phi) * Math.sin(theta),
      radius * Math.cos(phi),
    ));
  }

  const pointGeo = new THREE.BufferGeometry().setFromPoints(points);
  const pointMat = new THREE.PointsMaterial({ color: 0x0f5b4e, size: isCompact ? 0.075 : 0.06, transparent: true, opacity: 0.55, sizeAttenuation: true });
  group.add(new THREE.Points(pointGeo, pointMat));

  const accentPoints = points.filter((_, index) => index % 11 === 0).map((point) => point.clone().multiplyScalar(1.015));
  const accentGeo = new THREE.BufferGeometry().setFromPoints(accentPoints);
  const accentMat = new THREE.PointsMaterial({ color: 0xc96f4a, size: isCompact ? 0.1 : 0.085, transparent: true, opacity: 0.7, sizeAttenuation: true });
  group.add(new THREE.Points(accentGeo, accentMat));

  const linePositions = [];
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      if (points[i].distanceTo(points[j]) < 2.15) {
        linePositions.push(
          points[i].x, points[i].y, points[i].z,
          points[j].x, points[j].y, points[j].z,
        );
      }
    }
  }
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.Float32BufferAttribute(linePositions, 3));
  const lineMat = new THREE.LineBasicMaterial({ color: 0xc8a45c, transparent: true, opacity: 0.18 });
  group.add(new THREE.LineSegments(lineGeo, lineMat));

  let pointerX = 0;
  let pointerY = 0;
  let scrollProgress = 0;
  let visible = true;

  const resize = () => {
    const width = hero.clientWidth;
    const height = hero.clientHeight;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(height, 1);
    camera.updateProjectionMatrix();
  };

  const onPointer = (event) => {
    pointerX = (event.clientX / window.innerWidth - 0.5) * 2;
    pointerY = (event.clientY / window.innerHeight - 0.5) * 2;
  };
  const onScroll = () => {
    const rect = hero.getBoundingClientRect();
    scrollProgress = Math.min(1, Math.max(0, -rect.top / Math.max(rect.height, 1)));
  };

  resize();
  const observer = new ResizeObserver(resize);
  observer.observe(hero);
  const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }, { threshold: 0.01 });
  intersection.observe(hero);

  if (!reduced) {
    window.addEventListener("pointermove", onPointer, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    const clock = new THREE.Clock();
    const render = () => {
      window.requestAnimationFrame(render);
      if (!visible) return;
      const elapsed = clock.getElapsedTime();
      group.rotation.y = elapsed * 0.045 + scrollProgress * 0.75 + pointerX * 0.1;
      group.rotation.x = Math.sin(elapsed * 0.08) * 0.07 + pointerY * 0.06;
      group.position.y = -scrollProgress * 0.35;
      camera.position.z = 8 - scrollProgress * 0.7;
      renderer.render(scene, camera);
    };
    render();
  } else {
    renderer.render(scene, camera);
  }
}
