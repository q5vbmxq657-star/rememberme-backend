# Face Detection Model

YuNet `face_detection_yunet_2023mar.onnx`, distributed under the adjacent MIT license.
Source: https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet
SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`

Bundled with the backend; no runtime download or third-party image transfer.
OpenCV 4.12 runs the model on CPU with a 0.9 score threshold and 0.3 NMS threshold.
Input is bounded to 320 pixels on the longest edge; detected boxes are mapped back
to source dimensions. This keeps large close-up portraits within the model's
documented face-size range and bounds inference cost. The existing image and
video quality policies remain in place. This detects faces, not identity.

Run `pytest tests/test_avatar_media_analysis_service.py` before changing the model.
