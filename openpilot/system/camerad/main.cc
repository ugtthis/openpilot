#include "system/camerad/cameras/camera_common.h"

#include <cassert>

#include "common/params.h"
#include "common/util.h"

int main(int argc, char *argv[]) {
  // doesn't need RT priority since we're using isolcpus
  int ret = util::set_core_affinity({6});
  assert(ret == 0 || Params().getBool("IsOffroad")); // failure ok while offroad due to offlining cores

  std::string wide_sensor_mode = Params().get("CamcorderSensorMode");
  if (wide_sensor_mode.empty()) wide_sensor_mode = "stock";
  camerad_thread(wide_sensor_mode);
  return 0;
}
