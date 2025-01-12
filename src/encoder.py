from PyQt5.QtCore import QThread, pyqtSignal, QRunnable, pyqtSlot
from ffmpeg_progress_yield import FfmpegProgress
import utils
import subprocess
import shutil
import sys
import os



class encode(QThread):
    updateLabel = pyqtSignal(str)
    updateLabel_2 = pyqtSignal(list)
    updateLabel_6 = pyqtSignal(str)
    updateProgressBar = pyqtSignal(float)

    def __init__(self):
        super().__init__()

    # Method to pass data to the encode thread
    def passData(self, args):
        self.filePathList = args["filePathList"]
        self.mixAudio = args["mixAudio"]
        self.noAudio = args["noAudio"]
        self.normalizezAudio = args["normalizezAudio"]
        self.startTime = args["startTime"]
        self.endTime = args["endTime"]
        self.targetFileSize = args["targetFileSize"]
        self.ffmpeg = args["ffmpeg"]
        self.ffprobe = args["ffprobe"]
        self.jpegoptim = args["jpegoptim"]
        self.imageFormat = args["imageFormat"]
        self.audioFormat = args["audioFormat"]
        self.videoFormat = args["videoFormat"]
        
    def stop(self):
        self.running = False

    # Method to calculate the duration of the video based on user supplied start and end times
    def calculateDuration(self, duration):
        if self.startTime != "" and self.endTime == "":
            duration -= utils.convertTimeToSeconds(self.startTime)

            return ["-ss", self.startTime], duration

        elif self.startTime == "" and self.endTime != "":
            duration = utils.convertTimeToSeconds(self.endTime)

            return ["-to", self.endTime], duration

        elif self.startTime != "" and self.endTime != "":
            duration = utils.convertTimeToSeconds(self.endTime) - utils.convertTimeToSeconds(self.startTime)

            return ["-ss", self.startTime, "-to", self.endTime], duration

        else:
            return None, duration

    def calculateBitrate(self, fileInfo, container, duration, audioCodec, audioOnly, videoOnly, audioPath=None):
        # Came up with these values by using ffprobe -loglevel verbose and manually checking the muxing overhead of files
        if container == "mp4":
            containerConstOverhead = 1024 * 8
            containerpacketOverhead = 12 * 8

        elif container == "webm":
            containerConstOverhead = 1024 * 8   # MKV seems to use ~624 bytes however leaning on the safe side
            containerpacketOverhead = 12 * 8    # MKV seems to use ~6.25 to 9 bytes per packet however leaning on the safe side

        if self.videoFormat == "MP4 (H.264)":
            mult = 0.97

        elif self.videoFormat == "WEBM (VP9)":
            mult = 0.96

        elif self.videoFormat == "WEBM (AV1)":
            mult = 0.99

        if videoOnly == False:
            # Only encoding audio
            if audioOnly == True:
                if self.audioFormat == "WEBM (Video)":
                    containerConstOverhead = 1024 * 8   # MKV seems to use ~624 bytes however leaning on the safe side
                    containerpacketOverhead = 64 * 8    # MKV seems to use ~52 bytes per audio packet and ~12 per video packet
                    mult = 0.95

                elif self.audioFormat == "WEBM":
                    containerConstOverhead = 1024 * 8   # MKV seems to use ~624 bytes however leaning on the safe side
                    containerpacketOverhead = 52 * 8    # MKV seems to use ~52 bytes per audio packet
                    mult = 0.95

                elif self.audioFormat == "OGG":
                    containerConstOverhead = 1024 * 8   # OGG seems to use ~624 bytes however leaning on the safe side
                    containerpacketOverhead = 16 * 8    # OGG seems to use ~16 bytes per packet
                    mult = 0.95

                audioOverhead = (containerpacketOverhead * fileInfo["numberOfAudioPackets"]) + containerConstOverhead
                audioBitrate = int(((self.targetFileSize - audioOverhead) / duration) * mult)
                upperBound = 512000 # Anything higher than 512kbps is unsupported by Opus

            # Only encoding audio to be used in a video
            elif audioOnly == False:
                audioOverhead = containerpacketOverhead * fileInfo["numberOfAudioPackets"] + containerConstOverhead
                audioBitrate = int((((self.targetFileSize - audioOverhead) / 10) / duration) * mult) # 10 is arbitrary. It ensures that 90% of the file size is used for video, and 10% is used for audio
                upperBound = 128000 # 128kbps Opus audio is considered transparent by many people

            if fileInfo["audioBitrate"] <= audioBitrate and fileInfo["audioBitrate"] <= upperBound:
                if fileInfo["audioCodec"] == "opus" and self.normalizezAudio == False and (audioCodec == "opus" or audioCodec == None) and (self.mixAudio == False or fileInfo["audioStreams"] == 1):
                    audioBitrate = "copy"

                elif fileInfo["audioCodec"] == "aac" and self.normalizezAudio == False and (audioCodec == "aac" or audioCodec == None) and (self.mixAudio == False or fileInfo["audioStreams"] == 1):
                    audioBitrate = "copy"

                else:
                    audioBitrate = str(fileInfo["audioBitrate"])
            
            elif audioBitrate > upperBound:
                audioBitrate = str(upperBound)

            elif audioBitrate < 10000: # Opus quality degrades consideribly <10kbps
                audioBitrate = "10000"

            else:
                audioBitrate = str(audioBitrate)

            return audioBitrate

        # Only encoding video
        elif videoOnly == True:
            if audioPath == None:
                audioPath = 0

            else:
                audioPath = os.path.getsize(audioPath)

            videoOverhead = containerpacketOverhead * fileInfo["numberOfVideoPackets"] + containerConstOverhead
            videoBitrate = int(((self.targetFileSize - videoOverhead - (audioPath * 8)) / fileInfo["videoLength"]) * mult)

            return videoBitrate

    def encodeVideo(self, filePath, fileInfo, null, progressPercent, progressPercent2, progressPercent3):
        trimFlags, fileInfo["videoLength"] = self.calculateDuration(fileInfo["videoLength"])
        if fileInfo["videoLength"] <= 0:
            return "negativeDurationError"

        ffmpegCommand = [self.ffmpeg]
        if trimFlags != None:
            ffmpegCommand.extend(trimFlags)

        ffmpegCommand.extend(["-i", str(filePath)])

        print("Video length:", fileInfo["videoLength"])

        # Options for each video format.
        if self.videoFormat == "MP4 (H.264)":
            codec_flags = ["-preset", "veryslow", "-aq-mode", "3", "-c:v", "libx264"]
            container = "mp4"
            extentsion = "mp4"
            audioCodec = "aac"

        elif self.videoFormat == "WEBM (VP9)":
            codec_flags = ["-deadline", "good", "-cpu-used", "1", "-c:v", "libvpx-vp9", "-row-mt", "1"]
            container = "webm"
            extentsion = "webm"
            audioCodec = "libopus"

        elif self.videoFormat == "WEBM (AV1)":
            codec_flags = ["-cpu-used", "6", "-c:v", "libaom-av1", "-row-mt", "1"]
            container = "webm"
            extentsion = "webm"
            audioCodec = "libopus"

        # Check if the user wants to remove all audio tracks or if audio exists at all in the original file.
        if self.noAudio == True or fileInfo["audioStreams"] == 0:
            audioExists = False
            audioPath = None

        # Otherwise encode audio seperately from video.
        else:
            audioPath = self.encodeAudio(filePath, fileInfo, container, audioCodec, audioOnly=False)
            if audioPath == "error":
                return "error"

            audioExists = True

            ffmpegCommand.extend(["-i", str(audioPath), "-map", "1:a:0", "-c:a", "copy"])
            ffmpegCommand.extend(codec_flags)

        videoBitrate = self.calculateBitrate(fileInfo, container, fileInfo["videoLength"], audioCodec, audioOnly=False, videoOnly=True, audioPath=audioPath)
        # videoBitrate can't be negative.
        if videoBitrate <= 0:
            return "bitrateLowError"

        if container == "mp4":
            ffmpegCommand.extend(["-movflags", "+faststart"])

        fileSize = os.path.getsize(filePath)
        print("File size:", fileSize)

        heights = [2160, 2088, 2016, 1944, 1872, 1800, 1728, 1656, 1584, 1512, 1440, 1368, 1296, 1224, 1152, 1080, 1008, 936, 864, 792, 720, 648, 576, 504, 432, 360, 288, 144, 72] # Divisible by 8 16:9 resolutions
        bitPerPixel = 0.02 # 0.02 is arbitrary
        pixelsPerSecond = videoBitrate / bitPerPixel
        print(f"Pixels per second allowed {pixelsPerSecond}")

        currentPixelsPerSecond = (fileInfo["width"] * fileInfo["height"]) * fileInfo["framerate"]

        if currentPixelsPerSecond <= pixelsPerSecond:
            height = fileInfo["height"]

        elif currentPixelsPerSecond > pixelsPerSecond:
            if currentPixelsPerSecond >= (pixelsPerSecond * 4) and (fileInfo["framerate"] / 2) != 24: # 4 is arbitrary
                ffmpegCommand.extend(["-r", str(fileInfo["framerate"] / 2)])

            for testHeight in heights:
                testWidth = fileInfo["width"] * (testHeight / fileInfo["height"])
                testPixelsPerSecond = testWidth * testHeight * fileInfo["framerate"]

                if testPixelsPerSecond <= pixelsPerSecond and testHeight < fileInfo["height"]:
                    height = testHeight
                    break
                    
                # If testPixelsPerSecond is never below pixelsPerSecond then the program will crash. This is added to avoid that.
                else:
                    height = testHeight

        ffmpegCommand.extend(["-vf", "scale='trunc(oh*a/2)*2:{}':flags=bicubic,format=yuv420p".format(height)])

        if self.videoFormat == "MP4 (H.264)":
            videoCodec = "H.264"

        elif self.videoFormat == "WEBM (VP9)":
            videoCodec = "VP9"

        elif self.videoFormat == "WEBM (AV1)":
            videoCodec = "AV1"

        outputFile = fileInfo["dirName"] + fileInfo["fileName"] + "_FFmpeg2Discord_" + videoCodec + "." + extentsion

        ffmpegCommand.extend(["-map", "0:v", "-map_metadata", "-1", "-map_chapters", "-1", "-avoid_negative_ts", "make_zero", "-b:v", str(videoBitrate)])

        def ffmpeg2pass(flags, twoPass, progressPercent, progressPercent3):
            for flag in ffmpegCommand + flags:
                print(flag, end=" ", flush=True)
            print()

            ff = FfmpegProgress(ffmpegCommand + flags)
            for progress in ff.run_command_with_progress(utils.createNoWindow(), duration_override=fileInfo["videoLength"]):
                if self.running == False:
                    ff.quit()
                    break

                self.updateProgressBar.emit((progress / progressPercent) + progressPercent3)
                print(f"Encoding video: Pass {twoPass}: {progress:.2f}/100", end="\r")
            print()

        try:
            self.updateLabel_6.emit("Compressing video...")
            logFile = os.urandom(8).hex()
            ffmpeg2pass(flags=["-pass", "1", "-f", container, "-passlogfile", logFile, null, "-y"], twoPass=1, progressPercent=progressPercent, progressPercent3=0)
            if self.running == False:
                print("Operation was canceled by user")
                utils.cleanUp(logFile, audioPath, audioExists)

            ffmpeg2pass(flags=["-pass", "2", "-f", container, "-passlogfile", logFile, outputFile, "-y"], twoPass=2, progressPercent=progressPercent2, progressPercent3=progressPercent3)
            if self.running == False:
                print("Operation was canceled by user")
                utils.cleanUp(logFile, audioPath, audioExists)

        except Exception as e:
            print(f"General error: {e}")
            return "error"

        utils.cleanUp(logFile, audioPath, audioExists)

        return outputFile

    def encodeAudio(self, file, fileInfo, container=None, audioCodec=None, audioOnly=True, mult=1.0):
        trimFlags, fileInfo["audioDuration"] = self.calculateDuration(fileInfo["audioDuration"])
        if fileInfo["audioDuration"] <= 0:
            return "negativeDurationError"
        
        encodeAudioCommand = [self.ffmpeg]
        
        if trimFlags != None:
            encodeAudioCommand.extend(trimFlags)

        encodeAudioCommand.extend(["-i", str(file)])

        if audioOnly == True:
            outputFile = fileInfo["dirName"] + fileInfo["fileName"] + "_FFmpeg2Discord" + "."

            if self.audioFormat == "WEBM (Video)":
                audioCodec = "libopus"
                container = "webm"
                encodeAudioCommand.extend(["-f", "lavfi", "-i", "color=black:size=160x120", "-map", "1:v:0", "-r", "1", "-shortest", "-c:v", "libvpx-vp9"])

            elif self.audioFormat == "WEBM":
                audioCodec = "libopus"
                container = "webm"
                encodeAudioCommand.extend(["-vn"])

            elif self.audioFormat == "OGG":
                audioCodec = "libopus"
                container = "ogg"
                encodeAudioCommand.extend(["-vn"])

            outputFile += container

            audioBitrate = self.calculateBitrate(fileInfo, container, fileInfo["audioDuration"], audioCodec, audioOnly=True, videoOnly=False, audioPath=None)
            print(audioBitrate)
            audioBitrate = int(int(audioBitrate) * mult)

        else:
            outputFile = os.getcwd() + "/" + fileInfo["fileName"] + os.urandom(8).hex() + "."

            encodeAudioCommand.extend(["-vn"])
            outputFile += container # The audio file should use the same container format as the video to get an accurate idea on the file size. This is because Matroska has a higher muxing overhead for audio than something like Opus.

            audioBitrate = self.calculateBitrate(fileInfo, container, fileInfo["audioDuration"], audioCodec, audioOnly=False, videoOnly=False, audioPath=None)

        if audioBitrate == "copy":
            encodeAudioCommand.extend(["-c:a", str(audioBitrate)])

        else:
            encodeAudioCommand.extend(["-b:a", str(audioBitrate), "-c:a", audioCodec])

        if fileInfo["audioChannels"] >= 2 and audioBitrate != "copy":
            if int(audioBitrate) < 24000: # 24kbps is arbitary
                encodeAudioCommand.extend(["-ac", "1",])

            else:
                encodeAudioCommand.extend(["-ac", "2",])

        if self.normalizezAudio == True and (self.mixAudio == True and fileInfo["audioStreams"] > 1):
            encodeAudioCommand.extend(["-filter_complex", f"loudnorm,amerge=inputs={fileInfo['audioStreams']}[a]", "-map", "[a]"]) # Normalizes the first audio track only?

        elif self.normalizezAudio == True:
            encodeAudioCommand.extend(["-filter_complex", "loudnorm"])

        elif self.mixAudio == True and fileInfo["audioStreams"] > 1:
            encodeAudioCommand.extend(["-filter_complex", f"amerge=inputs={fileInfo['audioStreams']}[a]", "-map", "[a]"])

        else:
            encodeAudioCommand.extend(["-map", "0:a:0"])

        encodeAudioCommand.extend(["-map_metadata", "-1", str(outputFile), "-y"])

        for flag in encodeAudioCommand:
            print(flag, end=" ", flush=True)

        try:
            self.updateLabel_6.emit("Compressing audio...")
            ff = FfmpegProgress(encodeAudioCommand)
            for progress in ff.run_command_with_progress(utils.createNoWindow(), duration_override=fileInfo["audioDuration"]):
                if self.running == False:
                    ff.quit()
                    break

                self.updateProgressBar.emit(progress)
                print(f"Encoding audio: {progress:.2f}/100", end="\r")
            print()

        except Exception as e:
            print(f"General error: {e}")
            return "error"

        # Upon failure, attempts to re-encode audio with a lower bitrate due to the fact that targeting a specific file size for audio is highly inaccurate.
        if (os.path.getsize(outputFile) * 8) > self.targetFileSize:
            if audioBitrate < 10000:
                return "bitrateLowError"

            else:
                mult -= 0.05
                self.encodeAudio(file, fileInfo, container, audioCodec, audioOnly, mult)

        return outputFile

    def encodeImage(self, filePath, fileInfo):
        tempFilePath = os.getcwd() + "/" + fileInfo["fileName"] + os.urandom(8).hex() + fileInfo["fileExtension"]

        if self.imageFormat == "WEBP":
            extentsion = ".webp"
            container = "webp"
            encoder = "libwebp_anim"
            high = 100
            middle = int(high / 2)
            low = 0

            percent = 100.00 / 7 # Takes 8 tries to get the file size below the limit.

        elif self.imageFormat == "JPG":
            extentsion = ".jpg"
            container = "image2"
            encoder = "mjpeg"
            high = 31
            middle = int(high / 2)
            low = 1

            percent = 100.00 / 6 # Takes 7 tries to get the file size below the limit.

        self.updateLabel_6.emit("Compressing image...")
        print("Attempting to losslessly compress image below the size limit")
        
        try:
            if fileInfo["fileFormat"] == "jpeg":
                shutil.copy(filePath, tempFilePath) # jpegoptim is unable to change the output file name so shutil must handle it.
                outputFile = fileInfo["dirName"] + fileInfo["fileName"] + "_FFmpeg2Discord_Lossless.jpg"
                subprocess.run([self.jpegoptim, "--quiet", "--strip-all", tempFilePath], **utils.createNoWindow())

            elif fileInfo["fileFormat"] != "jpeg" and self.imageFormat == "WEBP":
                outputFile = fileInfo["dirName"] + fileInfo["fileName"] + "_FFmpeg2Discord_Lossless_" + extentsion
                subprocess.run([self.ffmpeg, "-hide_banner", "-v", "error", "-i", filePath, "-map_metadata", "-1", "-compression_level", "6", "-c:v", encoder, "-lossless", "1", "-f", container, tempFilePath, "-y"], **utils.createNoWindow())

            else:
                shutil.copy(filePath, tempFilePath)

        except Exception as e:
            print(f"Failed to perform initial image compression: {e}")
            return "error"

        if (os.path.getsize(tempFilePath) * 8) > self.targetFileSize:
            print("Lossless compression failed")
            print("Attempting to find the optimal quality level for size limit")
            outputFile = fileInfo["dirName"] + fileInfo["fileName"] + "_FFmpeg2Discord_" + extentsion
            progress = 0

            # Used binary search to find optimial quality value. Not ideal for speed but couldn't find a better way.
            try:
                while True:
                    if self.running == False:
                        break

                    print(f"Quality set to {middle}")
                    previousMiddle = middle

                    subprocess.run([self.ffmpeg, "-hide_banner", "-v", "error", "-i", filePath, "-map_metadata", "-1", "-compression_level", "6", "-c:v", encoder, "-q:v", str(middle), "-f", container, tempFilePath, "-y"], **utils.createNoWindow())

                    tempFileSize = os.path.getsize(tempFilePath) * 8
                    print(tempFileSize)
                    if tempFileSize > self.targetFileSize:
                        if self.imageFormat == "WEBP":
                            high = middle - 1

                        elif self.imageFormat == "JPG":
                            low = middle + 2 # Increased by two because middle always rounds down and for JPG lower means higher file size which leaves the file too big.

                    else:
                        if self.imageFormat == "WEBP":
                            low = middle + 1

                        elif self.imageFormat == "JPG":
                            high = middle - 1

                    middle = (high + low) // 2
                    if middle < 0: # Prevents the quality value from going below zero which would cause an error in ffmpeg.
                        middle = 0

                    progress += percent
                    self.updateProgressBar.emit(progress)

                    if previousMiddle == middle: # The binary search will converge on the optimal quality value. Once this happens the next middle value will be the same as the one before it.
                        break

            except Exception as e:
                print(f"Failed to encode image with quality {middle}: {e}")
                return "error"

        if self.running == True:
            try:
                shutil.move(tempFilePath, outputFile)
                return outputFile

            except Exception as e:
                print(f"Failed to move file: {e}")
                return "error"

    # Method to check if encoding exited successfully and to display any errors.
    def checkFile(self, outputFile, displayFilePathList, displayFilePath, currentIndex):
        def displayLogs(file, log, gui_log, color):
            print(log)
            print(file)
            self.updateLabel_6.emit(gui_log)
            displayFilePathList[currentIndex] = f"<font color={color}>" + displayFilePath + "</font><br>"
            self.updateLabel_2.emit(displayFilePathList)

        if outputFile == "error":
            displayLogs(displayFilePath, "An error has been caught with file", "Error with file", "red")

        elif outputFile == "negativeDurationError":
            displayLogs(displayFilePath, "Error: duration specified is negative", "Duration specified is negative", "red")

        elif outputFile == "bitrateLowError":
            displayLogs(displayFilePath, "Error: bitrate is too low to compress the file", "Bitrate is too low", "red")

        elif os.path.exists(outputFile) == False:
            displayLogs(outputFile, "Error: file does not exist", "Unable to locate file", "red")

        elif os.path.exists(outputFile) == True and os.path.getsize(outputFile) * 8 > self.targetFileSize:
            displayLogs(outputFile, "Error: compression failed to reduce file size below the maximum allowed limit", "Outputted file is too large", "red")

        else:
            displayLogs(outputFile, "Compression completed successfully!", "Compression completed successfully!", "green")

    def run(self):
        videoProgress = 0
        self.running = True
        segment = ""
        displayFilePathList = []

        if os.name == "posix": # Linux/Unix
            null = "/dev/null"

        elif os.name == "nt": # Windows
            null = "NUL"

        for displayFile in self.filePathList:
            displayFilePathList.extend([displayFile + "<br>"])

        for filePath in self.filePathList:
            if filePath:
                fileInfo = utils.getFileInfo(filePath, self.ffprobe, self.mixAudio)

                # Update GUI.
                numOfVideos = len(self.filePathList)
                displayFilePath = filePath
                currentIndex = self.filePathList.index(filePath)
                displayFilePathList[currentIndex] = "<font color='orange'>" + displayFilePath + "</font><br>"
                videoProgress += 1
                self.updateLabel.emit(str(videoProgress) + "/" + str(numOfVideos))
                self.updateLabel_2.emit(displayFilePathList)
                self.updateLabel_6.emit("displayFilePathList")
                self.updateProgressBar.emit(0.00)

                progressPercent = 2
                progressPercent2 = 2
                progressPercent3 = 50

                # If file is video, audio or image and encode respectively.
                try:
                    if fileInfo["fileType"] == "video" and fileInfo["videoStreams"] != 0:
                        outputFile = self.encodeVideo(filePath, fileInfo, null, progressPercent, progressPercent2, progressPercent3)

                    elif fileInfo["fileType"] == "audio" or (fileInfo["fileType"] == "video" and fileInfo["videoStreams"] == 0):                            
                        outputFile = self.encodeAudio(filePath, fileInfo, audioOnly=True)

                    elif fileInfo["fileType"] == "image":
                        outputFile = self.encodeImage(filePath, fileInfo)

                    elif fileInfo == "error":
                        outputFile = "error"
                
                except Exception as e:
                    print(f"Failed to encode image {filePath}: {e}")
                    outputFile = "error"

                # Check if encoding exited successfully.
                if self.running == True:
                    self.checkFile(outputFile, displayFilePathList, displayFilePath, currentIndex)

                else:
                    break