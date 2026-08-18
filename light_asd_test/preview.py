#!/usr/bin/env python3
"""录制前的摄像头实时预览窗口，方便确认取景和位置。
按 空格 确认开始录制，按 Q / ESC 取消。
"""
import sys
import time
import cv2

VIDEO_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def main():
    cap = cv2.VideoCapture(VIDEO_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print(f"无法打开摄像头 /dev/video{VIDEO_INDEX}")
        sys.exit(1)

    window = "摄像头预览 - 空格开始录制 / Q取消"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    started = False
    while True:
        ret, frame = cap.read()
        if not ret:
            print("读取摄像头画面失败")
            break
        cv2.putText(frame, "SPACE = start recording", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.putText(frame, "Q / ESC = cancel", (20, 75),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow(window, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord(' '):
            started = True
            break
        if key in (ord('q'), 27):  # q or ESC
            break

    cap.release()
    cv2.destroyAllWindows()
    cv2.waitKey(1)
    time.sleep(1)  # 让驱动完全释放v4l2设备句柄，避免和后续ffmpeg录制抢设备
    sys.exit(0 if started else 1)


if __name__ == "__main__":
    main()
