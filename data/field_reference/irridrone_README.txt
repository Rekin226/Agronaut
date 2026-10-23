IrriDrone Dataset
=================

Version: 1.0
License: Creative Commons Attribution 4.0 International (CC BY 4.0)


1. Overview
-----------

IrriDrone is a UAV-based video dataset of center-pivot and sprinkler irrigation
patterns recorded over three agricultural fields in Ghoruchay village, Dehgolan
County, Kurdistan Province, Iran. The dataset was prepared by the Department of
Computer Engineering, Faculty of Engineering, University of Kurdistan,
Sanandaj, Iran.

The videos show the wetted area produced by active sprinklers from a near-vertical
(top-down) aerial viewpoint. The data cover three crops, three flight altitudes,
and three sprinkler configurations. Each recording is provided in two paired
forms:

  1. a boundary-overlaid video, in which the estimated irrigation boundary is
     drawn as a white circular overlay; and
  2. the corresponding unannotated video.

The two versions of a recording have the same filename and are stored in
separate annotated and unannotated ZIP archives. They must not be interpreted
as independent observations.


2. Dataset Contents
-------------------

The release contains:

  - 674 boundary-overlaid MP4 videos;
  - 674 paired unannotated MP4 videos;
  - 1,348 MP4 files in total;
  - 18 ZIP archives containing the videos, grouped by farm, crop, sprinkler
    configuration, and annotation status;
  - metadata.csv, containing one row for each of the 674 paired recordings; and
  - README.txt, this document.

Archive names follow this pattern:

  farm[1-3]_[crop]_[3|6|9]sprinklers_[annotated|unannotated].zip

Because of file-size limits, the farm3 alfalfa archives are divided into part1,
part2, and part3. These parts belong to the same data group and are not
predefined training, validation, or test splits.

Farm-to-crop mapping:

  - farm1: carrot
  - farm2: potato
  - farm3: alfalfa

Note: Due to a clerical naming error during archive packaging, the farm3 archive filenames contain the legacy label “alfaalfa_3sprinklers.” These archives contain recordings from the alfalfa field acquired with 9 simultaneously active sprinklers. The “3sprinklers” label appears only in the archive filenames and does not reflect the actual irrigation configuration or affect the archive contents. The correct 9-active-sprinkler configuration is specified in metadata.csv and in the filenames of the individual video files contained within the ZIP archives.

3. Irrigation Configurations
----------------------------

The numbers 3, 6, and 9 in the archive names indicate the number of
simultaneously active sprinklers:

  - 3 active sprinklers: high relative pressure per sprinkler;
  - 6 active sprinklers: intermediate relative pressure per sprinkler; and
  - 9 active sprinklers: low relative pressure per sprinkler.

The pressure labels are relative operating categories for this experiment; they
are not direct measurements in bar, kPa, or another pressure unit.


4. Video Acquisition and Properties
-----------------------------------

Videos were acquired with a DJI Air 3S UAV. During recording, the UAV hovered
above the target riser and maintained a near-vertical view. Data were collected
at nominal flight altitudes of 35 m, 45 m, and 55 m.

The original recordings were made at 4K resolution and 120 frames per second and
were subsequently trimmed and standardized using VideoPad. The released files
have the following properties:

  - container: MP4;
  - video codec: H.264;
  - resolution: 1920 x 1080 pixels;
  - frame rate: 50 frames per second; and
  - clip duration: 12.54 to 81.28 seconds.

Original field recordings lasted approximately 3 to 5 minutes and included at
least two sprinkler rotations. Each released clip contains one complete
rotation. Clip start and end points therefore vary among recordings.

Collection did not necessarily occur on a single day. Some recordings were
continued on another day because the UAV battery was depleted. The date and time
associated with each recording are provided in metadata.csv.

The recordings were made under visually calm conditions, with no observable wind
disruption of the irrigation pattern. Wind speed was not instrumentally measured.


5. Discharge Measurement
------------------------

Sprinkler discharge was measured immediately after the corresponding original
video recording using a 20 L volumetric method. The container fill time was
measured in three trials, and the mean fill time was used to calculate discharge:

  Mean discharge (L/s) = 20 / mean fill time (s)

No inline flowmeter was used. Discharge values are temporally associated with,
and matched to, the corresponding irrigation configuration; they are not
frame-level synchronized measurements.


6. Boundary Annotation
-----------------------

One team member drew a white circular boundary overlay around the estimated
irrigated area using a Samsung Galaxy Tab A SM-P205 tablet, its S Pen, and the
built-in video player. Two additional team members reviewed the annotations, and
the final boundary was accepted by consensus.

The annotation is embedded directly in the boundary-overlaid video. Separate
pixel masks, polygon files, or frame-level annotation files are not included.
No formal inter-annotator agreement score or IoU value is provided.


7. Metadata File
----------------

metadata.csv contains 674 data rows and the following 16 columns:

  Video_Name_mp4
      Video identifier. The .mp4 extension is omitted in the metadata values;
      append .mp4 when matching a row to a video file. The boundary-overlaid and
      unannotated versions share this identifier.

  Crop
      Crop present in the field: carrot, potato, or alfalfa.

  Active_Sprinklers
      Number of simultaneously active sprinklers: 3, 6, or 9.

  Pressure
      Relative pressure category per sprinkler: high, intermediate, or low.

  Mean_Discharge_lps
      Mean sprinkler discharge in litres per second (L/s), calculated from the
      three volumetric fill-time trials.

  Altitude_m
      Nominal UAV flight altitude in metres: 35, 45, or 55.

  Date
      Recording date in day/month/year format.

  Time
      Local recording time.

  Time_Period
      Broad local time category: morning, noon, or evening.

  Duration_s
      Exact duration of the released video clip in seconds.

  Resolution
      Released video resolution in pixels; all records are 1920 x 1080.

  FPS
      Released video frame rate; all records are 50 frames per second.

  Latitude
      Latitude of the recording location in decimal degrees (WGS 84).

  Longitude
      Longitude of the recording location in decimal degrees (WGS 84).

  Annotated
      Indicates whether the catalogued video has the embedded boundary overlay.
      All metadata rows are marked Yes. The matching unannotated version is
      stored separately with the same video identifier.

  Status
      Data-quality/release status. All records in this release are marked Valid.


8. File Matching and Recommended Use
------------------------------------

To locate a recording:

  1. read Video_Name_mp4 in metadata.csv;
  2. append the .mp4 extension;
  3. use Crop and Active_Sprinklers to select the corresponding ZIP archive; and
  4. select the annotated archive for the boundary-overlaid file or the matching
     unannotated archive for its paired file.

Because both versions originate from the same recording, keep each pair together
when creating training, validation, and test partitions. Placing the two versions
of one recording in different partitions would introduce data leakage.

No official training, validation, or test partition is supplied. Users should
define splits appropriate to their application and report the splitting strategy
in any derived study.


9. Known Limitations
--------------------

  - Pressure is represented by relative categories rather than instrumented
    pressure measurements.
  - Discharge measurements are configuration-matched and temporally associated,
    but are not synchronized at the video-frame level.
  - Wind speed was not measured instrumentally.
  - Boundary annotations are embedded overlays; separate masks or polygons are
    not supplied.
  - Collection dates and times vary, and some acquisition sessions continued on
    another day because of UAV battery limitations.


10. License and Attribution
---------------------------

This dataset is released under the Creative Commons Attribution 4.0 International
license (CC BY 4.0). You may share and adapt the material for any purpose,
provided that appropriate credit is given, a link to the license is supplied,
and changes are indicated.

License text: https://creativecommons.org/licenses/by/4.0/

When using this dataset, please cite the accompanying dataset article "IrriDrone: Overhead Drone-Based Video Dataset for Precision Sprinkler Irrigation Assessment" or official
repository record. If a formal citation has not yet been assigned, cite the
dataset title, authors, version, University of Kurdistan, year, and persistent
repository identifier when available.
Explain