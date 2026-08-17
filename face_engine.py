"""慧签 face engine: MediaPipe 检测+关键点(眨眼) + dlib/face_recognition 识别"""
import os
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
_detector = None
_landmarker = None


def _load_detector():
    global _detector
    if _detector is None:
        base = mp_python.BaseOptions(model_asset_path=os.path.join(MODELS, 'face_detector.tflite'))
        _detector = vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(base_options=base, min_detection_confidence=0.4))
    return _detector


def _load_landmarker():
    global _landmarker
    if _landmarker is None:
        base = mp_python.BaseOptions(model_asset_path=os.path.join(MODELS, 'face_landmarker.task'))
        _landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(base_options=base, num_faces=2,
                                         min_face_detection_confidence=0.5,
                                         min_tracking_confidence=0.5))
    return _landmarker


def _to_mp_image(bgr):
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def detect_faces(bgr):
    """返回 [(x, y, w, h), ...]"""
    res = _load_detector().detect(_to_mp_image(bgr))
    return [(d.bounding_box.origin_x, d.bounding_box.origin_y,
             d.bounding_box.width, d.bounding_box.height) for d in res.detections]


def face_landmarks(bgr):
    """每个脸的 478 个关键点 [(x, y, vis), ...]; 无则 []"""
    res = _load_landmarker().detect(_to_mp_image(bgr))
    return [[(lm.x, lm.y, lm.visibility) for lm in f] for f in res.face_landmarks]


_EYE_LEFT = [33, 160, 158, 133, 153, 144]
_EYE_RIGHT = [362, 385, 387, 263, 373, 380]


def _ear(pts, idx):
    a = float(np.linalg.norm(pts[idx[1]] - pts[idx[5]]))
    b = float(np.linalg.norm(pts[idx[2]] - pts[idx[4]]))
    c = float(np.linalg.norm(pts[idx[0]] - pts[idx[3]]))
    return (a + b) / (2.0 * c + 1e-6)


def blink_ratio(landmarks):
    """EAR 均值, 越小越像闭眼"""
    pts = np.array([(lm[0], lm[1]) for lm in landmarks], dtype=np.float32)
    return (_ear(pts, _EYE_LEFT) + _ear(pts, _EYE_RIGHT)) / 2.0


def is_blinking(landmarks, threshold=0.20):
    return blink_ratio(landmarks) < threshold


def crop_face(bgr, box, margin=0.3):
    x, y, w, h = box
    hh, ww = bgr.shape[:2]
    mx, my = int(w * margin), int(h * margin)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(ww, x + w + mx), min(hh, y + h + my)
    return bgr[y0:y1, x0:x1]


def get_embedding(face_bgr):
    """dlib/face_recognition 128 维特征; 无人脸返回 None"""
    import face_recognition
    h, w = face_bgr.shape[:2]
    if h > 300:
        scale = 300.0 / h
        face_bgr = cv2.resize(face_bgr, (max(1, int(w * scale)), 300))
    rgb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
    encs = face_recognition.face_encodings(rgb)
    return encs[0] if encs else None


def compare(emb, ref_emb):
    """欧氏距离(越小越像)"""
    import face_recognition
    return float(face_recognition.face_distance([ref_emb], emb)[0])


def get_embedding_multi(frame, box=None):
    """容错提特征: 裁剪HOG -> 整帧HOG多尺寸 -> 缩小帧CNN兜底"""
    import face_recognition
    if box is not None:
        emb = get_embedding(crop_face(frame, box))
        if emb is not None:
            return emb
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    for scale in (1.0, 0.5, 0.35, 0.25):
        im = rgb if scale == 1.0 else cv2.resize(rgb, (0, 0), fx=scale, fy=scale)
        encs = face_recognition.face_encodings(im)
        if encs:
            return encs[0]
    # CNN 兜底(缩小帧, 快且对角度/大小更稳)
    for scale in (0.35, 0.25):
        im = cv2.resize(rgb, (0, 0), fx=scale, fy=scale)
        try:
            locs = face_recognition.face_locations(im, model='cnn')
            if locs:
                encs = face_recognition.face_encodings(im, locs, model='cnn')
                if encs:
                    return encs[0]
        except Exception:
            continue
    return None


NOSE_TIP = 1


def nose_x(landmarks):
    """鼻尖归一化 x 坐标 (0~1)"""
    return landmarks[NOSE_TIP][0]


def head_shake_detected(nose_xs, range_thresh=0.03, min_turns=2):
    """摇头检测: 鼻尖 x 有左右摆动 (范围够大 + 方向反转够多次)"""
    if len(nose_xs) < 4:
        return False
    if max(nose_xs) - min(nose_xs) < range_thresh:
        return False
    turns = 0
    prev_dir = 0
    for i in range(1, len(nose_xs)):
        d = nose_xs[i] - nose_xs[i - 1]
        if abs(d) < 1e-4:
            continue
        cur = 1 if d > 0 else -1
        if prev_dir != 0 and cur != prev_dir:
            turns += 1
        prev_dir = cur
    # 大摆幅(>=0.06) 1 次反向即可; 一般摆幅需 2 次反向
    if max(nose_xs) - min(nose_xs) >= 0.06:
        return turns >= 1
    return turns >= min_turns


def get_embedding_cnn(face_bgr):
    """CNN 提特征(对角度/大小更稳, 较慢, 用于兜底)"""
    import face_recognition
    rgb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
    h, w = face_bgr.shape[:2]
    if h > 320:
        s = 320.0 / h
        rgb = cv2.resize(rgb, (max(1, int(w * s)), 320))
    locs = face_recognition.face_locations(rgb, model='cnn')
    if locs:
        encs = face_recognition.face_encodings(rgb, locs, model='cnn')
        if encs:
            return encs[0]
    return None
