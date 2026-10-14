#include "system/camerad/cameras/camera_common.h"

#include <cassert>

#include "common/params.h"
#include "common/swaglog.h"
#include "common/util.h"

int main(int argc, char *argv[]) {
  // doesn't need RT priority since we're using isolcpus
  int ret = util::set_core_affinity({6});
  // Offroad, hardwared brings core 6 back online within a loop of the camera being requested.
  for (int i = 0; ret != 0 && i < 20; i++) {
    util::sleep_for(100);
    ret = util::set_core_affinity({6});
  }
  if (ret != 0) LOGE("camerad could not pin to core 6, frames may drop");
  assert(ret == 0 || Params().getBool("IsOffroad")); // failure ok while offroad due to offlining cores

  std::string wide_sensor_mode = Params().get("CamcorderSensorMode");
  if (wide_sensor_mode.empty()) wide_sensor_mode = "stock";
  camerad_thread(wide_sensor_mode);
  return 0;
}
