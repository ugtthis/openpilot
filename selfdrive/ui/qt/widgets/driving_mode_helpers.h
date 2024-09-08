#pragma once
#include "common/params.h"
#include <QObject>

enum class DrivingMode {
  Chill,
  Experimental,
  StockADAS,
};

class DrivingModePanel;  // Forward declaration

DrivingMode getCurrentDrivingMode(Params& params);
void setDrivingMode(Params& params, DrivingMode mode, QObject* panel = nullptr);
bool hasDrivingModeChanged(Params& params);