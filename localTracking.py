import cv2
import numpy as np
from scipy import spatial
import threading
from kalmanFilt import KalmanTrack
from HSConfig import kalmanParams, flowParams, featureParams, colourParams, trackingParams

__author__ = 'Michael Holmes'

# ----------------------------------------------------------------
#
# Class to handle all local tracking related queries, utilises the
# local Kalman filter class
#
# TODO: Alternative markup method (non-window) + target save
# TODO: Outlier rejection in minimum enclosing circle calc
# TODO: Add toggle for showing track history
# ----------------------------------------------------------------


# ----------------------------------------------------------------
#               GLOBALS
# ----------------------------------------------------------------
target0 = [(-1, -1), (-1, -1), False, (-1, -1)]  # Drives mouse events


# Mouse event for local mode markup functions
def onMouse(event, x, y, flags, param):
    global target0
    if event == cv2.EVENT_LBUTTONDOWN:
        target0[0] = (x, y)
        target0[2] = True
    elif event == cv2.EVENT_MOUSEMOVE:
        if target0[2]:
            target0[3] = (x, y)
    elif event == cv2.EVENT_LBUTTONUP:
        target0[1] = (x, y)
        cv2.destroyWindow('Choose Target')


class LocalModeOne(KalmanTrack, threading.Thread):
    def __init__(self, camRef, targetNumber):
        threading.Thread.__init__(self)

        # Markup Target
        self._camRef = camRef
        KalmanTrack.__init__(self, **kalmanParams)
        self._currObs = np.zeros((2, 1))
        self._colourParams = colourParams
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                 (colourParams['kernelSize'], colourParams['kernelSize']))

        # Add Optical Flow and feature detection params
        self._colourLock = False
        self._colourTargetRadius = colourParams['colourTargetRadius']
        self._flowLock = False
        self._flowTrackHistoryMax = flowParams['trackLen']
        del flowParams['trackLen']
        self._flowTracks = []
        self._flowParams = flowParams
        self._featureParams = featureParams

        # Markup Target
        self._hsvLimits = []
        self._markup_target()
        del self._colourParams

        # Update filter with initial positions, image resolution and target colour profile
        self.x[0] = self._currObs[0]
        self.x[1] = self._currObs[1]

        # Target lock params
        self.targetLocked = False
        self._cyclesUnlocked = 0
        self._maxFailedCycles = trackingParams['maxFailedCycles']
        self._currTimestamp = None
        self._deadZone = trackingParams['deadZone']
        self._shiftMulti = trackingParams['shiftMulti']

        # Thread params
        self._killThread = False
        self._trackingLock = threading.Lock()
        self._targetNumber = targetNumber
        self._targetRead = False
        self.setDaemon(True)
        self.start()
        return

    def run(self):
        while not self._killThread and self._camRef.is_opened():
            # Run local tracker, tracking target position with the filter

            # Get new image
            self._camRef.get_frame()
            currFrame, currTimestamp, currDeltaTime = self._camRef.current_frame()
            # Update tracker elapsed times
            self.update_elapsed_counters(currDeltaTime)

            # Run prediction stage if prediction increment has elapsed
            if self.predict_stage_elapsed():
                self._trackingLock.acquire()
                self._currTimestamp = currTimestamp
                self.predict()
                self._targetRead = False
                self._trackingLock.release()

            # Run update stage if update increment has elapsed
            if self.update_stage_elapsed():
                self._new_obs_from_im(currFrame.copy())
                if self.targetLocked:
                    self._trackingLock.acquire()
                    self._currTimestamp = currTimestamp
                    self.update(self.get_current_obs())
                    self._targetRead = False
                    self._trackingLock.release()

            # Push latest filter estimate to image window along with new image
            # if __debug__:
                    #     currEstimate = self.observe_model()
                    #     currEstimate = (currEstimate[0], currEstimate[1])
                    #     frameCopy = currFrame.copy()
                    #     if self.targetLocked:  # Green circle
                    #         circColour = (0, 255, 0)
                    #     else:  # Red circle
                    #         circColour = (0, 0, 255)
                    #     cv2.circle(frameCopy, currEstimate, self._colourTargetRadius, circColour)
                    #     titleString = 'Target %d' % self._targetNumber
                    #     cv2.imshow(titleString, frameCopy)

        return

    def stop(self):
        self._killThread = True
        return

    def get_pos_data(self):
        if self.targetLocked and not self._targetRead:
            self._trackingLock.acquire()
            packet = (self._targetNumber, self._currTimestamp, self.x[0], self.x[1],
                      np.mean([self.P[0, 0] / self.maxUV[0], self.P[1, 1] / self.maxUV[1]]))
            self._targetRead = True
            self._trackingLock.release()
        else:
            packet = None
        return packet

    def _markup_target(self):
        global target0, frameImg
        cv2.waitKey(3000)
        self._camRef.get_frame()
        self._camRef.get_frame()  # Doubled to initialise time-step
        currFrame = self._camRef.current_frame()[0]
        self.maxUV = (currFrame.shape[1], currFrame.shape[0])

        # Select target region
        cv2.imshow('Choose Target', currFrame)
        cv2.setMouseCallback('Choose Target', onMouse)
        while target0[1][0] == -1:
            currFrame = self._camRef.current_frame()[0]
            if not target0[2]:
                cv2.imshow('Choose Target', currFrame)
                self._camRef.get_frame()
            else:
                if not (target0[3][0] == -1):
                    frameCopy = currFrame.copy()
                    cv2.rectangle(frameCopy, target0[0], target0[3], (0, 255, 0))
                    cv2.imshow('Choose Target', frameCopy)
                else:
                    cv2.imshow('Choose Target', currFrame)
            if cv2.waitKey(50) & 0xFF == ord(' '):
                cv2.destroyWindow('Choose Target')
                return
        bounds = self._set_roi(target0)
        self._currObs[0] = bounds[2]
        self._currObs[1] = bounds[5]

        # Extract target HSV
        if bounds[0] == bounds[1] or bounds[3] == bounds[4]:
            self._set_hsv_limits(currFrame[bounds[5], bounds[2]])
        else:
            self._set_hsv_limits(currFrame[bounds[3]:bounds[4], bounds[0]:bounds[1]])

        # Initialise keypoints to track
        greyIm = cv2.cvtColor(currFrame, cv2.COLOR_BGR2GRAY)
        mask = np.zeros_like(greyIm)
        cv2.rectangle(mask, target0[0], target0[3], 255, -1)
        p = cv2.goodFeaturesToTrack(greyIm, mask=mask, **self._featureParams)
        if p is not None:
            for x, y in np.float32(p).reshape(-1, 2):
                self._flowTracks.append([(x, y)])
        self._prevIm = greyIm
        return

    # Get bounding box from selected points
    def _set_roi(self, target0):
        bounds = []
        bounds.append(int(np.minimum(target0[0][0], target0[1][0])))
        bounds.append(int(np.maximum(target0[0][0], target0[1][0])))
        bounds.append(int(np.mean((target0[0][0], target0[1][0]))))
        bounds.append(int(np.minimum(target0[0][1], target0[1][1])))
        bounds.append(int(np.maximum(target0[0][1], target0[1][1])))
        bounds.append(int(np.mean((target0[0][1], target0[1][1]))))
        return bounds

    def _set_hsv_limits(self, bgr):
        # Correct for lone pixel selection
        if len(bgr) == 3:
            bgr = bgr.reshape(1, 1, 3)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hueMean = np.mean(hsv[:, :, 0])
        satMean = np.mean(hsv[:, :, 1])
        self._hsvLimits.append(self._bound_hue_limits(hueMean - self._colourParams['hueWindow']))
        self._hsvLimits.append(self._bound_hue_limits(hueMean + self._colourParams['hueWindow']))
        self._hsvLimits.append(self._bound_sat_limits(satMean - self._colourParams['satWindow']))
        self._hsvLimits.append(self._bound_sat_limits(satMean + self._colourParams['satWindow']))
        return

    def _bound_hue_limits(self, hue):
        while hue < 0 or hue > 180:
            if hue < 0:
                hue += 180
            elif hue > 180:
                hue -= 180
        return int(hue)

    def _bound_sat_limits(self, sat):
        if sat < 0:
            sat = 0
        elif sat > 255:
            sat = 255
        return int(sat)

    def get_current_obs(self):
        return self._currObs

    def _new_obs_from_im(self, image):
        # Get new measurement from colour extraction
        colourObs = self._new_colour_obs(image)
        # Get new measurement from sparse optical flow
        flowObs = self._new_flow_obs(image, colourObs)
        # Return point and update current member
        if self._colourLock and self._flowLock and not self._observation_discontinuous(colourObs, flowObs):
            self._currObs[0] = np.mean((colourObs[0], flowObs[0]))
            self._currObs[1] = np.mean((colourObs[1], flowObs[1]))
            self._cyclesUnlocked = 0
        elif self._flowLock:
            self._currObs = flowObs
            self._cyclesUnlocked = 0
        else:
            self._cyclesUnlocked += 1
        if self._cyclesUnlocked < self._maxFailedCycles:
            self.targetLocked = True
        else:
            self.targetLocked = False
        return

    def _observation_discontinuous(self, colourObs, flowObs):
        # Check distance between them
        distance = spatial.distance.pdist(np.hstack((colourObs, flowObs)).T)[0]
        if distance > self._shiftMulti * self._colourTargetRadius:
            return True
        newPos = np.array([np.mean((colourObs[0], flowObs[0])), np.mean((colourObs[1], flowObs[1]))]).reshape((1, 2))
        prevEst = self.observe_model().reshape((1, 2))
        distance = spatial.distance.pdist(np.vstack((newPos, prevEst)))[0]
        velocity = np.linalg.norm([self.x[2], self.x[3]])
        if distance > self._deadZone and distance > self._shiftMulti * velocity:
            return True
        return False

    def _new_colour_obs(self, image):
        # Convert new frame to HSV
        newHSV = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        # Threshold Target
        newHSV = self._threshold_target(newHSV)
        # Morphological Opening
        newHSV = cv2.morphologyEx(newHSV, cv2.MORPH_OPEN, self._kernel)
        # Morphological Closing
        newHSV = cv2.morphologyEx(newHSV, cv2.MORPH_CLOSE, self._kernel)
        # Find largest blob
        _, contours, hierarchy = cv2.findContours(newHSV, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        targetOut = self._check_contours(contours)
        return targetOut

    def _threshold_target(self, origHSV):
        # Circular colour spaces are a pain
        if self._hsvLimits[1] >= self._hsvLimits[0]:
            newHSV = cv2.inRange(origHSV,
                                 np.array([self._hsvLimits[0], self._hsvLimits[2], 0]),
                                 np.array([self._hsvLimits[1], self._hsvLimits[3], 255]))
        else:
            newHSV = cv2.inRange(origHSV,
                                 np.array([self._hsvLimits[0], self._hsvLimits[2], 0]),
                                 np.array([180, self._hsvLimits[3], 255])) | \
                     cv2.inRange(origHSV,
                                 np.array([0, self._hsvLimits[2], 0]),
                                 np.array([self._hsvLimits[1], self._hsvLimits[3], 255]))
        return newHSV

    def _check_contours(self, contours):
        if len(contours):
            # Generate centroids list
            candidates = np.zeros((len(contours), 2))
            for idx in range(len(contours)):
                moments = cv2.moments(contours[0])
                if moments['m00'] == 0:
                    continue
                candidates[idx, 0] = moments['m10'] / moments['m00']
                candidates[idx, 1] = moments['m01'] / moments['m00']
            # Remove failed candidates
            candidates = candidates[candidates > 0].reshape(-1, 2)
            if len(candidates) == 0:
                self._colourLock = False
                return None
            # Find centroid closest to current estimate
            kdTree = spatial.cKDTree(candidates)
            prevEst = self.x[:2].T
            dist, idx = kdTree.query(prevEst)
            targetOut = candidates[idx[0], :2].reshape((2, 1))
            self._colourLock = True
            self._colourTargetRadius = int(np.sqrt(moments['m00'] / np.pi)) + colourParams['colourTargetRadius']
            return targetOut
        else:
            self._colourLock = False
            return None

    def _new_flow_obs(self, image, colourObs):
        greyIm = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        newTracks = []
        # Run tracker if points present, used from OpenCV example
        if len(self._flowTracks) > 0:
            img0, img1 = self._prevIm, greyIm
            p0 = np.float32([tr[-1] for tr in self._flowTracks]).reshape(-1, 1, 2)
            p1, st, err = cv2.calcOpticalFlowPyrLK(img0, img1, p0, None, **self._flowParams)
            p0r, st, err = cv2.calcOpticalFlowPyrLK(img1, img0, p1, None, **self._flowParams)
            d = abs(p0 - p0r).reshape(-1, 2).max(-1)
            good = d < 1
            for tr, (x, y), good_flag in zip(self._flowTracks, p1.reshape(-1, 2), good):
                if not good_flag:
                    continue
                tr.append((x, y))
                if len(tr) > self._flowTrackHistoryMax:
                    del tr[0]
                newTracks.append(tr)
            self._flowTracks = newTracks
            self._prevIm = greyIm
            if len(newTracks):
                cent, rad = cv2.minEnclosingCircle(np.float32([tr[-1] for tr in newTracks]).reshape(-1, 1, 2))
                self._flowLock = True
                targetOut = np.array([cent[0], cent[1]]).reshape((2, 1))
                return targetOut
        elif self._colourLock:  # Find new points corresponding to colour lock
            mask = np.zeros_like(greyIm)
            cv2.circle(mask, (colourObs[0], colourObs[1]), self._colourTargetRadius, 255, -1)
            p = cv2.goodFeaturesToTrack(greyIm, mask=mask, **self._featureParams)
            if p is not None:
                for x, y in np.float32(p).reshape(-1, 2):
                    self._flowTracks.append([(x, y)])
            self._prevIm = greyIm

        self._flowLock = False
        return None
