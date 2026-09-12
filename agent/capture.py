"""
capture.py -- couche de perception visuelle : capture d'ecran DX12 a faible latence.

Mesure sur cette machine : dxcam, sortie 0 = 3840x1080 (le seul ecran actif, les
moniteurs virtuels VR ne sont pas des sorties dxcam). Premiere capture ~230 ms
(initialisation), puis ~5 ms.

Le mode video (start()) livre toujours une image : sans lui, grab() renvoie None des que
l'ecran n'a pas change, ce qui compliquerait tout le code appelant.

Les regions d'interet (ROI) sont exprimees en FRACTIONS de l'ecran pour survivre a un
changement de resolution. Les valeurs par defaut sont des estimations pour le HUD de
Cyberpunk en 32:9 : A CALIBRER sur une vraie capture avant de s'y fier.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import dxcam
import numpy as np


@dataclass(frozen=True)
class Roi:
    """Rectangle en fractions [0,1] de la largeur/hauteur : (x0, y0, x1, y1)."""
    x0: float
    y0: float
    x1: float
    y1: float

    def cut(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        return frame[int(self.y0 * h):int(self.y1 * h), int(self.x0 * w):int(self.x1 * w)]


# Estimations HUD Cyberpunk 2077, ecran 32:9. Le HUD est ancre aux bords, donc en 32:9
# les elements sont tres ecartes : le centre de l'ecran est presque vide.
ROIS: dict[str, Roi] = {
    'full':       Roi(0.00, 0.00, 1.00, 1.00),
    'center':     Roi(0.30, 0.15, 0.70, 0.85),   # zone de jeu utile, sans les bords HUD
    'minimap':    Roi(0.86, 0.02, 0.99, 0.30),   # en haut a droite
    'quest':      Roi(0.78, 0.30, 0.99, 0.48),   # objectif de quete, sous la minimap
    'health':     Roi(0.01, 0.86, 0.20, 0.98),   # vie / endurance, en bas a gauche
    'weapon':     Roi(0.80, 0.86, 0.99, 0.98),   # arme / munitions, en bas a droite
    'subtitles':  Roi(0.30, 0.78, 0.70, 0.90),   # sous-titres, bas centre
    'dialogue':   Roi(0.30, 0.55, 0.70, 0.92),   # choix de dialogue, bas centre
}


class Screen:
    """Capture continue de l'ecran principal."""

    def __init__(self, output_idx: int = 0, fps: int = 30) -> None:
        self._cam = dxcam.create(output_idx=output_idx, output_color='BGR')
        # video_mode=True : get_latest_frame() renvoie toujours la derniere image
        self._cam.start(target_fps=fps, video_mode=True)
        self.width, self.height = self._cam.width, self._cam.height

    def frame(self) -> np.ndarray:
        return self._cam.get_latest_frame()

    def roi(self, name: str, frame: np.ndarray | None = None) -> np.ndarray:
        f = frame if frame is not None else self.frame()
        return ROIS[name].cut(f)

    @staticmethod
    def shrink(frame: np.ndarray, max_width: int = 1024) -> np.ndarray:
        """Reduit pour le modele de vision : moins de pixels = moins de latence."""
        h, w = frame.shape[:2]
        if w <= max_width:
            return frame
        scale = max_width / w
        return cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)

    @staticmethod
    def to_jpeg(frame: np.ndarray, quality: int = 80) -> bytes:
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError('encodage JPEG echoue')
        return buf.tobytes()

    def save_calibration(self, path: str) -> None:
        """Sauve une capture avec les ROI dessinees : sert a les ajuster a l'oeil."""
        f = self.frame().copy()
        h, w = f.shape[:2]
        for name, r in ROIS.items():
            if name == 'full':
                continue
            p0 = (int(r.x0 * w), int(r.y0 * h))
            p1 = (int(r.x1 * w), int(r.y1 * h))
            cv2.rectangle(f, p0, p1, (0, 255, 0), 2)
            cv2.putText(f, name, (p0[0] + 4, p0[1] + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imwrite(path, f)

    def close(self) -> None:
        self._cam.stop()


if __name__ == '__main__':
    import time
    s = Screen()
    print(f'ecran {s.width}x{s.height}')
    time.sleep(0.5)
    t0 = time.perf_counter()
    for _ in range(30):
        _ = s.frame()
    print(f'30 images en {(time.perf_counter() - t0) * 1000:.0f} ms')
    s.save_calibration('calibration_rois.png')
    print('calibration_rois.png ecrit : ouvre-le pour verifier les rectangles.')
    s.close()
