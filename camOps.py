import cv2
import numpy as np
import time
import threading
from HSConfig import camParams
__author__ = 'Michael Holmes'

# ----------------------------------------------------------------
#
# Class to handle all camera-related operations such as
# calibration, updating frames and responding to frame requests
#
# ----------------------------------------------------------------

# ----------------------------------------------------------------
#               GLOBALS
# ----------------------------------------------------------------
target0 = (-1, -1)


def onMouse(event, x, y, flags, param):
    global target0
    if flags & cv2.EVENT_FLAG_LBUTTON:
        target0 = x, y
    return


class CamHandler(threading.Thread):
    def __init__(self, camID=None):
        threading.Thread.__init__(self)
        # If ID is given, check if valid and open camera
        self._frame = None
        self._camLock = threading.Lock()
        self._lastTimestamp = 0  # Time in seconds since epoch (epoch and accuracy dependent on platform)
        self._deltaTime = 0  # Time in seconds between camera frames
        if camID is not None:
            self._camObj = cv2.VideoCapture(camID)
            if not self._camObj.isOpened():
                self._camObj = self._assign_cam()
        else:
            self._camObj = self._assign_cam()
        # Lock resolution at 480p for now if possible
        self._camObj.set(cv2.CAP_PROP_FRAME_WIDTH, camParams['capWidth'])
        self._camObj.set(cv2.CAP_PROP_FRAME_HEIGHT, camParams['capHeight'])
        self._updateFrame = True
        self._killThread = False
        self.setDaemon(True)
        self.start()
        return

    def run(self):
        while not self._killThread:
            if self._updateFrame:
                self._camLock.acquire()
                self._get_frame()
                self._updateFrame = False
                self._camLock.release()
        return

    def stop(self):
        self._killThread = True
        self._release_cam()
        return

    def get_frame(self):
        self._camLock.acquire()
        self._updateFrame = True
        self._camLock.release()
        return

    def _get_frame(self):
        self._camObj.grab()
        newTimestamp = time.time()
        self._frame = self._camObj.retrieve()[1]
        self._deltaTime = newTimestamp - self._lastTimestamp
        self._lastTimestamp = newTimestamp
        return

    def current_frame(self):
        self._camLock.acquire()
        frame = self._frame
        lastTimestamp = self._lastTimestamp
        deltaTime = self._deltaTime
        self._camLock.release()
        return frame, lastTimestamp, deltaTime

    def _assign_cam(self):
        # Enumerate available cameras
        imgList = []
        currID = 0
        camRef = cv2.VideoCapture()
        while True:
            camRef.open(currID)
            if not camRef.isOpened():
                break
            imgList.append(camRef.read()[1])
            camRef.release()
            currID += 1
            # Limit preview to 3 cameras
            if currID == 3:
                break
        # If no cameras available raise exception
        if not len(imgList):
            print 'IOError: No cameras found.'
            raise IOError
        # Spawn camera images and choose appropriate camera
        camRef.open(self._choose_cam(imgList, 1024, 768))
        return camRef

    def _choose_cam(self, imgList, xPx, yPx):
        global target0
        # Tessellates up to 3 images into a single image and returns that
        # image of size xPx pixels (width) by yPx pixels (height)
        imX = xPx / len(imgList)
        outImg = np.zeros((yPx, xPx, 3), np.uint8)
        for idx, img in enumerate(imgList):
            img = cv2.resize(img, (imX, yPx))
            outImg[:, (idx * imX):((idx + 1) * imX), :] = img
        cv2.imshow("Camera Options - Click image to choose", outImg)
        cv2.setMouseCallback("Camera Options - Click image to choose", onMouse)
        while target0[0] == -1:
            cv2.waitKey(1000)
        cv2.destroyWindow("Camera Options - Click image to choose")
        return target0[0] / imX

    def is_opened(self):
        return self._camObj.isOpened()

    def _release_cam(self):
        return self._camObj.release()

