#!/usr/bin/env python
#
# Copyright (C) 2019-2020 dRaster, Inc. - All Rights Reserved

from __future__ import print_function

import os
myDir = os.path.dirname(os.path.realpath(__file__))
myDir += "/deps"

import sys
sys.path.insert(0, myDir)

import time
from niraclient import NiraClient, NiraUploadInfo, NiraJobStatus, isoUtcDateParse
import argparse
import requests.exceptions
import datetime
import traceback
import json

# To verify we're using the bundled copy of requests
#print(requests.__file__)

parser = argparse.ArgumentParser(description='Nira Client CLI')
parser.add_argument('--apikey', required=True, type=str)
parser.add_argument('--url', required=True, type=str)
parser.add_argument('--useremail', type=str, default='', help="Specifies the user account that certain API operations occur under. For example, if an asset upload is performed, that user's name will appear in the `Uploader` column of Nira's asset listing page. If this argument is not provided, the first admin user found in the user database will be used.")
parser.add_argument('--upload-threads', dest='uploadthreads', type=int, default=4, help="Number of simultaneous upload connection threads to use. Using mulitple simultaneous connections for uploads can accelerate them significantly, particularly over long-distance WAN links.")
parser.add_argument('--upload-chunk-size', dest='uploadchunksize', type=int, default=1024 * 1024 * 10, help="Size of each uploaded chunk, in bytes. When uploading, files will be divided into chunks of this size and sent to the Nira server using the number of threads specified by the --upload-threads option.")

group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--upload', dest="asset_path", default=[], nargs='+', type=str, help='Takes a space separated list of file paths to upload, uploads them, then prints a URL to the resulting asset. The first file path specified should be a primary scene file (ma, mb, zpr, etc). Subsequent file paths should be accompanying files, such as textures.')
group.add_argument('--download', dest="download", default=[], nargs=2, type=str, help='Takes two parameters: An asset\'s URL (or the asset\'s short UUID) and a local destination folder to store the asset. The asset and all of its accompanying assets will be downloaded into this folder.')
group.add_argument('--set-metadata', dest="set_metadata_asset_url", default='', type=str, help='Takes an asset\'s URL (or the asset\'s short UUID), reads metadata JSON from stdin, and attaches this metadata to the asset on the Nira server. Also see --metadata-level.')
group.add_argument('--get-metadata', dest="get_metadata_asset_url", default='', type=str, help='Takes an asset\'s URL (or the asset\'s short UUID) and returns metadata for the asset or assetversion. Also see --metadata-level.')
parser.add_argument('--metadata-level', dest='metadata_level', choices=["assetversion", "asset"], default='assetversion', help='When using --set-metadata or --get-metadata, specifying "--metadata-level assetversion" or "--metadata-level asset" controls whether to set/retrieve the metadata attached to the assetversion specified, or the entire asset.')
parser.add_argument('--is-sequence', action='store_true', dest='is_sequence', help='If specified, when using --upload, defines that the assets are part of an animated sequence.')
parser.add_argument('--compress-textures', action='store_true', dest='compress_textures', help='If specified, when using --upload, compresses textures on the server.')
parser.add_argument('--wait-for-asset-processing', dest='wait_max_seconds', default=0, type=int, help='If specified, when using --upload, wait up to WAIT_MAX_SECONDS for the asset to be processed on the server before returning. If this argument is not provided, the command will return immediately after upload, and asset processing may not have finished yet. If an error occurs, the command will exit with a non-zero status and print an error message.')
group.add_argument('--show-updated-assets-every', dest='update_seconds', default=0, type=int, help='Polls the server every UPDATE_SECONDS, showing any asset updates that have occurred since the last poll. The command does not exit unless it encounters an error or is interrupted by the user.')
group.add_argument('--show-updated-assets-within', dest='seconds_ago', default=0, type=int, help='Show any asset updates that have occurred within SECONDS_AGO, then exit.')

args = parser.parse_args()

nirac = NiraClient(args.url, args.apikey, userEmail=args.useremail, uploadThreadCount=args.uploadthreads, uploadChunkSize=args.uploadchunksize)

def formatAssetUpdates(assetsData, lastUpdateTime):
  formattedAssetUpdates = []

  """
   An asset data record looks like this:
  {
    'status': 'processed',
    'uuid': 'adb693ff-3e7b-4827-b7f0-36867dab17aa',
    'approvalStatus': 'needs_review',
    'filename': 'dragon_attack.mb',
    'newestMarkupTime': '2019-05-13T04:14:53.163Z',
    'version': 2,
    'createdAt': '2019-04-11T10:15:52.152Z',
    'uploader': 'admin',
    'updatedAt': '2019-05-13T04:14:53.146Z',
    'subassetCount': '0',
    'openMarkupCount': '7',
    'urlUuid': 'rbaT_z57SCe38DaGfasXqg'
  }
  """

  # We can format this into a friendlier format as below.
  for assetData in assetsData:
    updateOutput  = ""
    updateOutput += "Asset: {} (version: {})\n".format(assetData['filename'], assetData['version'])

    newestMarkupTime = 0
    if assetData['newestMarkupTime'] is not None:
      newestMarkupTime = isoUtcDateParse(assetData['newestMarkupTime'])
    updatedAt = isoUtcDateParse(assetData['updatedAt'])
    createdAt = isoUtcDateParse(assetData['createdAt'])

    if newestMarkupTime and newestMarkupTime > lastUpdateTime:
      updateOutput += "\tNew Markups at {:%Y/%m/%d %H:%M:%S} UTC!\n".format(newestMarkupTime) # Note: Can use pytz or similar to get local times if desired.
    elif createdAt > lastUpdateTime:
      updateOutput += "\tUploaded at {:%Y/%m/%d %H:%M:%S} UTC!\n".format(createdAt)
    elif updatedAt > lastUpdateTime:
      updateOutput += "\tUpdated at {:%Y/%m/%d %H:%M:%S} UTC!\n".format(updatedAt)

    if assetData['status'] != 'processed':
      updateOutput += "\tStatus:\n".format(assetData['status'])

    updateOutput += "\tApproval Status: {}\n".format(assetData['approvalStatus'])
    if assetData['openMarkupCount']:
      updateOutput += "\tMarkups: {}\n".format(assetData['openMarkupCount'])
    updateOutput += "\tURL: {}\n".format(nirac.formatAssetUrl(assetData['urlUuid']))

    formattedAssetUpdates.append(updateOutput)

  return formattedAssetUpdates

try:
  if len(args.asset_path) > 0:
    uploadInfo = nirac.uploadAsset(args.asset_path, isSequence=args.is_sequence, compressTextures=args.compress_textures)

    if args.wait_max_seconds > 0:
      processingStatus = nirac.waitForAssetProcessing(uploadInfo.assetJobId, timeoutSeconds = args.wait_max_seconds)
      if processingStatus == NiraJobStatus.Processed:
        print(uploadInfo.assetUrl)
        sys.exit(0)
      else:
        print(processingStatus)
        sys.exit(1)
    else:
      print(uploadInfo.assetUrl)
      sys.exit(0)
  elif len(args.set_metadata_asset_url):
    metadataStr = ''
    for line in sys.stdin:
      metadataStr += line.rstrip()
    nirac.setAssetMetadata(args.set_metadata_asset_url, args.metadata_level, metadataStr)
  elif len(args.get_metadata_asset_url):
    metadataDict = nirac.getAssetMetadata(args.get_metadata_asset_url, args.metadata_level)
    print(json.dumps(metadataDict))
  elif len(args.download) == 2:
    nirac.downloadAsset(args.download[0], args.download[1])
  elif args.seconds_ago:
    sinceDate = datetime.datetime.utcnow() - datetime.timedelta(seconds=args.seconds_ago)
    assets = nirac.getAssetsUpdatedSince(sinceDate)
    formattedUpdates = formatAssetUpdates(assets, sinceDate)
    for formattedUpdate in formattedUpdates:
      print(formattedUpdate)
  elif args.update_seconds:
    lastUpdateTime = datetime.datetime.utcnow()

    while True:
      updateTime = datetime.datetime.utcnow()
      assets = nirac.getAssetsUpdatedSince(lastUpdateTime)
      formattedUpdates = formatAssetUpdates(assets, lastUpdateTime)

      for formattedUpdate in formattedUpdates:
        print(formattedUpdate)

      time.sleep(args.update_seconds)
      lastUpdateTime = updateTime

except requests.exceptions.HTTPError as error:
  print(error, file=sys.stderr)
  print(error.response.text, file=sys.stderr)
  sys.exit(1)
except (KeyboardInterrupt, SystemExit):
  raise
except Exception as e:
  print(traceback.format_exc(), file=sys.stderr)
  sys.exit(1)
