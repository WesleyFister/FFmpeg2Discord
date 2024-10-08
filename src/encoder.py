from PyQt5.QtCore import QThread, pyqtSignal, QRunnable, pyqtSlot
from ffmpeg_progress_yield import FfmpegProgress
from platform import system
import utils
import subprocess
import mimetypes
import shutil
import json
import sys
import os



class encode(QThread):
    updateLabel = pyqtSignal(str)
    updateLabel_2 = pyqtSignal(list)
    updateProgressBar = pyqtSignal(float)
    
    def __init__(self):
        super().__init__()
    
    def passData(self, filePathList, ffmpegMode, mixAudio, noAudio, startTime, endTime, targetFileSize, ffmpeg, ffprobe, jpegoptim):
        self.filePathList = filePathList
        self.ffmpegMode = ffmpegMode
        self.mixAudio = mixAudio
        self.noAudio = noAudio
        self.startTime = startTime
        self.endTime = endTime
        self.targetFileSize = targetFileSize
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.jpegoptim = jpegoptim
        
    def stop(self):
        self.running = False

    def encodeAudio(self, file, fileInfo, audioPath, audioOverhead, mult, audioOnly):
        trimFlags, duration = self.calculateDuration(fileInfo["audioDuration"])
        encodeAudioCommand = [self.ffmpeg]
        if trimFlags != None:
            encodeAudioCommand.extend(trimFlags)

        encodeAudioCommand.extend(["-i", str(file), "-vn", "-map_metadata", "-1"])

        if audioOnly == True:
            audioBitrate = int(((self.targetFileSize - audioOverhead) / duration) * mult)
            upperBound = 512000 # Anything higher than 512kbps is unsupported by Opus
            container = "ogg"

        else:
            audioBitrate = int((((self.targetFileSize - audioOverhead) / 10) / duration) * mult) # 10 is arbitrary. It ensures that 90% of the file size is used for video, and 10% is used for audio
            upperBound = 128000 # 128kbps Opus audio is considered transparent by many people
            container = "mka"
            
        if fileInfo["audioBitrate"] <= audioBitrate and fileInfo["audioBitrate"] <= upperBound:
            if fileInfo["audioCodec"] == "opus" and (self.mixAudio == False or fileInfo["audioStreams"] == 1):
                encodeAudioCommand.extend(["-c:a", "copy"])

            else:
                encodeAudioCommand.extend(["-b:a", str(fileInfo["audioBitrate"]), "-c:a", "libopus"])
        
        elif audioBitrate > upperBound:
            encodeAudioCommand.extend(["-b:a", str(upperBound), "-c:a", "libopus"])

        elif audioBitrate < 10000: # Opus quality degrades consideribly at 10kbps
            encodeAudioCommand.extend(["-b:a", "10000"], "-c:a", "libopus")

        else:
            encodeAudioCommand.extend(["-b:a", str(audioBitrate), "-c:a", "libopus"])

        if fileInfo["audioChannels"] >= 2:
            encodeAudioCommand.extend(["-ac", "2",])

            if audioBitrate < 24000: # 24kbps is arbitary
                encodeAudioCommand.extend(["-ac", "1",])

        if self.mixAudio == True and fileInfo["audioStreams"] > 1:
            encodeAudioCommand.extend(["-filter_complex", f"[0:a]amerge=inputs={fileInfo["audioStreams"]}[a]", '-map', '[a]'])
            
        else:
            encodeAudioCommand.extend(["-map", "0:a:0"])

        encodeAudioCommand.extend([str(audioPath), "-y"])
        print(encodeAudioCommand)

        ff = FfmpegProgress(encodeAudioCommand)
        for progress in ff.run_command_with_progress(duration_override=duration, **utils.createNoWindow()):
            if self.running == False:
                ff.quit()
                break

            self.updateProgressBar.emit(progress)
            print(f"Encoding audio: {progress:.2f}/100", end="\r")
        print()

        return audioPath

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

    def run(self):
        videoProgress = 0
        self.running = True
        segment = ""
        displayFilePathList = []
                                    # Came up with these values by using ffprobe -loglevel verbose and manually checking the muxing overhead of files
        mkvConstOverhead = 1024 * 8 # MKV seems to use ~624 bytes however leaning on the safe side
        mkvpacketOverhead = 12 * 8  # MKV seems to use ~6.25 to 9 bytes per packet however leaning on the safe side
        mult = 0.98                 # This is to add room for error. Even with two pass encoding bitrate will average higher than the amount specified
        
        for displayFile in self.filePathList:
            displayFilePathList.extend([displayFile + '<br>'])
        
        for filePath in self.filePathList:
            if filePath:
                dirName, file = os.path.split(filePath)
                fileName, fileExtension = os.path.splitext(file)
                dirName = dirName + '/'

                # Update GUI.
                numOfVideos = len(self.filePathList)
                displayFilePath = filePath
                currentIndex = self.filePathList.index(filePath)
                displayFilePathList[currentIndex] = '<font color="orange">' + displayFilePath + '</font><br>'
                videoProgress += 1
                self.updateLabel.emit(str(videoProgress) + "/" + str(numOfVideos))
                self.updateLabel_2.emit(displayFilePathList)
                self.updateProgressBar.emit(0.00)

                fileType, fileFormat = utils.mimeType(filePath)

                if fileType == "image" or fileType == "audio" or fileType == "video":
                    if fileType == "image":
                        tempFilePath = os.getcwd() + "/" + fileName + os.urandom(8).hex() + fileExtension
                        if self.ffmpegMode == "fastest":
                            compression_level = 0

                        elif self.ffmpegMode == "slow":
                            compression_level = 4

                        elif self.ffmpegMode == "slowest":
                            compression_level = 6

                        print("Attempting to losslessly compress image below the size limit")
                        if fileFormat == "jpeg":
                            shutil.copy(filePath, tempFilePath) # jpegoptim is unable to change the output file name so shutil must handle it.
                            outputFile = dirName + fileName + "_FFmpeg2Discord_Lossless.jpg"
                            subprocess.run([self.jpegoptim, "--quiet", "--strip-all", tempFilePath], **utils.createNoWindow())

                        elif fileFormat != "jpeg":
                            outputFile = dirName + fileName + "_FFmpeg2Discord_Lossless_" + self.ffmpegMode + ".webp"
                            subprocess.run([self.ffmpeg, "-hide_banner", "-v", "error", "-i", filePath, "-map_metadata", "-1", "-compression_level", str(compression_level), "-c:v", "libwebp_anim", "-lossless", "1", "-f", "webp", tempFilePath, "-y"], **utils.createNoWindow())

                        if (os.path.getsize(tempFilePath) * 8) > self.targetFileSize:
                            print("Lossless compression failed")
                            print("Attempting to find the optimal quality level for size limit")
                            outputFile = dirName + fileName + "_FFmpeg2Discord_" + self.ffmpegMode + ".webp"
                            progress = 0
                            high = 100
                            middle = high / 2
                            low = 0
                            while True: # Used binary search to find optimial quality value. Not ideal for speed but couldn't find a better way.
                                if self.running == False:
                                    break

                                print(f"Quality set to {middle}")
                                previousMiddle = middle

                                subprocess.run([self.ffmpeg, "-hide_banner", "-v", "error", "-i", filePath, "-map_metadata", "-1", "-compression_level", str(compression_level), "-c:v", "libwebp_anim", "-quality", str(middle), "-f", "webp", tempFilePath, "-y"], **utils.createNoWindow())

                                tempFileSize = os.path.getsize(tempFilePath) * 8
                                if tempFileSize > self.targetFileSize:
                                    high = middle - 1

                                else:
                                    low = middle + 1

                                middle = (high + low) // 2

                                progress += 14.28
                                self.updateProgressBar.emit(progress)

                                if previousMiddle == middle: # The binary search will converge on the optimal quality value after seven iterations. Once this happens the next middle value will be the same as the one before it.
                                    break

                        if self.running == True:
                            shutil.move(tempFilePath, outputFile)

                    if fileType == "audio" or fileType == "video":
                        fileInfo = utils.getFileInfo(filePath, self.mixAudio)

                        if fileType == "audio" or fileInfo["videoStreams"] == 0:
                            audioOverhead = mkvpacketOverhead * fileInfo["numberOfAudioPackets"] + mkvConstOverhead
                            outputFile = dirName + fileName + "_FFmpeg2Discord_" + self.ffmpegMode + ".ogg"
                            self.encodeAudio(filePath, fileInfo, outputFile, audioOverhead, mult, audioOnly=True)

                        if fileType == "video" and fileInfo["videoStreams"] != 0:
                            videoOverhead = mkvpacketOverhead * fileInfo["numberOfVideoPackets"] + mkvConstOverhead
                            trimFlags, fileInfo["videoLength"] = self.calculateDuration(fileInfo["videoLength"])

                            ffmpegCommand = [self.ffmpeg]
                            if trimFlags != None:
                                ffmpegCommand.extend(trimFlags)

                            ffmpegCommand.extend(["-i", str(filePath)])

                            print("Video length:", fileInfo["videoLength"])

                            if self.noAudio == True or fileInfo["audioStreams"] == 0:
                                videoBitrate = int(((self.targetFileSize - videoOverhead) / fileInfo["videoLength"]) * mult)

                                audioExists = False
                                audioPath = ""

                                ffmpegCommand.extend(["-an"])

                            else:
                                audioOverhead = mkvpacketOverhead * fileInfo["numberOfAudioPackets"] + mkvConstOverhead
                                audioPath = os.getcwd() + "/" + fileName + os.urandom(8).hex() + ".mka" # Since the video container is Matroska the audio file should also use the same container format to get an accurate idea on the file size. This is because Matroska has a higher muxing overhead for audio than something like Opus.
                                self.encodeAudio(filePath, fileInfo, audioPath, audioOverhead, mult, audioOnly=False)

                                videoBitrate = int(((self.targetFileSize - videoOverhead - (os.path.getsize(audioPath) * 8)) / fileInfo["videoLength"]) * mult)
                                audioExists = True

                                ffmpegCommand.extend(["-i", str(audioPath), "-map", "1:a:0", "-c:a", "copy"])

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

                            '''
                            if fileInfo["videoLength"] <= 30:
                                filePathBlank =  os.getcwd() + "/" + "blank.mkv"

                                command = [self.ffmpeg, "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=60:d=30", "-c:v", "libx264", filePathBlank, "-y"]
                                subprocess.check_output(command, **utils.createNoWindow())

                                filePathBlankMerged =  os.getcwd() + "/" + fileName + "blank.mkv"

                                command = [self.ffmpeg, "-i", filePath, "-i", filePathBlank, "-filter_complex", "concat=n=2:v=1:a=0", "-c:v", "libx264", filePathBlankMerged, "-y"]
                                subprocess.check_output(command, **utils.createNoWindow())

                                filePath = filePathBlankMerged
                            '''

                            ffmpegCommand.extend(["-vf", "scale='trunc(oh*a/2)*2:{}':flags=bicubic,format=yuv420p".format(height)])

                            # FFmpeg commands based on user selected options.
                            if self.ffmpegMode == "fastest":
                                ffmpegCommand.extend(["-preset", "veryfast", "-aq-mode", "3", "-c:v", "libx264"])

                                progressPercent = 2
                                progressPercent2 = 2
                                progressPercent3 = 50

                            elif self.ffmpegMode == "slow":
                                ffmpegCommand.extend(["-preset", "veryslow", "-aq-mode", "3", "-c:v", "libx264"])

                                progressPercent = 5
                                progressPercent2 = (5/4)
                                progressPercent3 = 20

                            elif self.ffmpegMode == "slowest":
                                ffmpegCommand.extend(["-deadline", "best", "-c:v", "libvpx-vp9", "-tile-columns", "0", "-auto-alt-ref", "1", "-lag-in-frames", "25", "-enable-tpl", "1"])

                                progressPercent = 5
                                progressPercent2 = (5/4)
                                progressPercent3 = 20

                            outputFile = dirName + fileName + "_FFmpeg2Discord_" + self.ffmpegMode + ".webm" # Does not actually use the WebM container. Instead it uses Matroska because it is a lightweight container format taking up less space than something like MP4. Discord will not embed the video if the extension is mkv.

                            ffmpegCommand.extend(["-map", "0:v", "-map_metadata", "-1", "-map_chapters", "-1", "-avoid_negative_ts", "make_zero", "-b:v", str(videoBitrate)])

                            def ffmpeg2pass(flags, twoPass, progressPercent, progressPercent3):
                                '''
                                for flag in ffmpegCommand + flags:
                                    print(flag, end=" ", flush=True)
                                print()
                                '''

                                ff = FfmpegProgress(ffmpegCommand + flags)
                                for progress in ff.run_command_with_progress(duration_override=fileInfo["videoLength"], **utils.createNoWindow()):
                                    if self.running == False:
                                        ff.quit()
                                        break

                                    self.updateProgressBar.emit((progress / progressPercent) + progressPercent3)
                                    print(f"Encoding video: Pass {twoPass}: {progress:.2f}/100", end="\r")
                                print()

                            ffmpeg2pass(flags=["-pass", "1", "-f", "null", "-", "-y"], twoPass=1, progressPercent=progressPercent, progressPercent3=0)
                            if self.running == False:
                                print("Operation was canceled by user")
                                utils.cleanUp(audioPath, audioExists)
                                break

                            ffmpeg2pass(flags=["-pass", "2", "-f", "matroska", outputFile, "-y"], twoPass=2, progressPercent=progressPercent2, progressPercent3=progressPercent3)
                            if self.running == False:
                                print("Operation was canceled by user")
                                utils.cleanUp(audioPath, audioExists)
                                break

                    if self.running == True:
                        if os.path.exists(outputFile) == True:
                            if (os.path.getsize(outputFile) * 8) > self.targetFileSize:
                                print("Compression failed: File is too large.")
                                print(outputFile)
                                displayFilePathList[currentIndex] = '<font color="red">' + displayFilePath + '</font><br>'
                                self.updateLabel_2.emit(displayFilePathList)

                            else:
                                print("Compression completed successfully!")
                                print(outputFile)
                                self.updateProgressBar.emit(100.00)
                                displayFilePathList[currentIndex] = '<font color="green">' + displayFilePath + '</font><br>'
                                self.updateLabel_2.emit(displayFilePathList)
