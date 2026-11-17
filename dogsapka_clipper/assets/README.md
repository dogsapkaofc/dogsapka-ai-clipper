# Bundled assets

* `face_detection_yunet_2023mar.onnx` – YuNet face detector (OpenCV Zoo,
  https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet, MIT licence).
  SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
  Used by `processing/layout.py` for facecam detection; it finds faces in profile / looking down,
  which OpenCV's frontal Haar cascade misses.
