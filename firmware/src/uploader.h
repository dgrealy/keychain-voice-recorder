// Sends pending notes to the Mac receiver over Wi-Fi (docs/PROTOCOL.md section 2).
#pragma once

namespace uploader {

enum class Result { Done, NoWifi, NoReceiver, Failed, Aborted };

// Connect, sync the clock, upload every pending note (oldest first) and turn Wi-Fi off.
// `shouldAbort` is polled throughout; returning true (button pressed) stops at once.
Result syncPending(bool (*shouldAbort)());

const char* describe(Result r);

}  // namespace uploader
