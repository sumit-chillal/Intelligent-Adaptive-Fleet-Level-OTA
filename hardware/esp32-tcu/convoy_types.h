// ===========================================================================
// CONVOY — types and file-scope state.
//
// WHY THIS IS A HEADER AND NOT PART OF THE .ino
//
// Arduino generates a prototype for every function in the sketch and inserts
// them immediately after the last #include line. A type declared in the .ino
// is therefore declared BELOW those prototypes, so any function whose
// signature mentions it produces:
//
//     error: variable or field 'setLed' declared void
//     error: 'LedState' was not declared in this scope
//
// on a line nobody wrote. Moving the declaration further up the .ino does not
// help, because the insertion point is above all of it.
//
// A header does fix it: #include lines are processed before the prototypes are
// inserted, so anything declared here is visible to them.
// ===========================================================================
#pragma once

#include <Arduino.h>

#include <vector>

// ------------------------------------------------------------------- LEDs --
// One meaning per colour, never two lit at once.
// LED_REVERTED blinks rather than holding steady. With three colours and four
// meanings, one has to be distinguishable some other way -- and a blink is the
// right one to spend it on, because an automatic revert is the state an
// observer is least likely to be expecting and most needs to notice.
enum LedState { LED_IDLE, LED_BUSY, LED_FAULT, LED_REVERTED, LED_OFF };

// -------------------------------------------------------------- OTA state --
// One in-flight update. `active` guards every chunk handler: a chunk arriving
// outside an accepted offer is a stray from a cancelled campaign and must be
// dropped rather than written to flash.
struct OtaSession {
  bool active = false;
  String campaignId;
  String firmwareId;
  String version;
  uint32_t versionCode = 0;
  uint32_t chunkCount = 0;
  uint32_t chunkSize = 0;
  uint32_t sizeBytes = 0;
  uint32_t nextIndex = 0;
  bool isRollback = false;

  // SHA-256 of each chunk, stored as RAW BYTES rather than hex Strings.
  //
  // 128 Arduino Strings of 64 characters cost roughly 10 KB once per-object
  // overhead is counted, and each is a separate heap allocation that fragments
  // the space the chunk buffers need. 128 x 32 raw bytes is 4 KB in one
  // contiguous block.
  std::vector<uint8_t> chunkHashes;   // chunkCount * 32 bytes
  String wholeSha256;
};

// ------------------------------------------------------------ reason codes --
// The same closed vocabulary the server and the Python simulator use. A shared
// taxonomy is what lets one query explain an outcome regardless of which kind
// of device produced it.
namespace Reason {
constexpr const char* SUCCESS = "SUCCESS";
constexpr const char* LOW_BATTERY = "FAILED_LOW_BATTERY";
constexpr const char* CHUNK_HASH = "FAILED_CHUNK_HASH_MISMATCH";
constexpr const char* IMAGE_HASH = "FAILED_IMAGE_HASH_MISMATCH";
constexpr const char* SIG_INVALID = "FAILED_SIGNATURE_INVALID";
constexpr const char* ANTI_ROLLBACK = "FAILED_ANTI_ROLLBACK";
constexpr const char* FLASH_WRITE = "FAILED_FLASH_WRITE";
constexpr const char* ROLLED_BACK_MANUAL = "ROLLED_BACK_MANUAL";
}  // namespace Reason
