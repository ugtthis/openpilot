#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"
#include "selfdrive/ui/qt/offroad/driving_mode_panel.h"

DrivingMode getCurrentDrivingMode(Params& params) {
  bool openpilotEnabled = params.getBool("OpenpilotEnabledToggle");
  bool experimentalMode = params.getBool("ExperimentalMode");

  if (!openpilotEnabled) return DrivingMode::StockADAS;
  return experimentalMode ? DrivingMode::Experimental : DrivingMode::Chill;
}

void setDrivingMode(Params& params, DrivingMode mode, DrivingModePanel* panel) {
  DrivingMode currentMode = getCurrentDrivingMode(params);
  if (currentMode != mode) {
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
    if (panel) {
      emit panel->drivingModeChanged();
    }
  }
}
