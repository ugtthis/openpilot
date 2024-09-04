#pragma once

#include "common/params.h"

enum class DrivingMode {
  StockADAS,
  Chill,
  Experimental
};

DrivingMode getCurrentDrivingMode();
void setDrivingMode(DrivingMode mode);
