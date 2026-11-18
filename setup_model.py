"""Download the official MediaPipe Pose Landmarker Lite model."""

from urllib.request import urlretrieve

from media import MODEL


if __name__ == "__main__":
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
    temporary = MODEL.with_suffix(".download")
    urlretrieve(url, temporary)
    temporary.replace(MODEL)
    print(f"Modelo disponível em {MODEL}")

