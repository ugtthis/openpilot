#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"

DrivingMode getCurrentDrivingMode() {
  Params params;
  bool openpilotEnabled = params.getBool("OpenpilotEnabledToggle");
  bool experimentalMode = params.getBool("ExperimentalMode");

  if (!openpilotEnabled) {
    return DrivingMode::StockADAS;
  } else if (experimentalMode) {
    return DrivingMode::Experimental;
  } else {
    return DrivingMode::Chill;
  }
}

void setDrivingMode(DrivingMode mode) {
  Params params;
  switch (mode) {
    case DrivingMode::StockADAS:
      params.putBool("OpenpilotEnabledToggle", false);
      params.putBool("ExperimentalMode", false);
      break;
    case DrivingMode::Chill:
      params.putBool("OpenpilotEnabledToggle", true);
      params.putBool("ExperimentalMode", false);
      break;
    case DrivingMode::Experimental:
      params.putBool("OpenpilotEnabledToggle", true);
      params.putBool("ExperimentalMode", true);
      break;
  }
}
