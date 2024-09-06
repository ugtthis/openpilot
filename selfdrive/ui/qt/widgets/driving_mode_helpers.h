#pragma once
#include "common/params.h"

enum class DrivingMode {
  Chill,
  Experimental,
  StockADAS,
};

class DrivingModePanel;  // Forward declaration

DrivingMode getCurrentDrivingMode(Params& params);
void setDrivingMode(Params& params, DrivingMode mode, DrivingModePanel* panel = nullptr);