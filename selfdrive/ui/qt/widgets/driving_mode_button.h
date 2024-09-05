#pragma once

#include <QPushButton>
#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"

class DrivingModeButton : public QPushButton {
  Q_OBJECT

public:
  DrivingModeButton(QString text, DrivingMode mode, Params& params, QWidget* parent = nullptr);
  void updateState();

private slots:
  void onClicked();

signals:
  void drivingModeChanged();

private:
  DrivingMode mode;
  Params& params;
};
