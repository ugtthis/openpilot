#pragma once

#include <QWidget>
#include <QVBoxLayout>
#include <QTimer>
#include "selfdrive/ui/qt/widgets/driving_mode_button.h"

class DrivingModePanel : public QWidget {
  Q_OBJECT

signals:
  void drivingModeChanged();

public:
  DrivingModePanel(QWidget* parent = nullptr);

private slots:
  void updateButtons();
  void checkParamsAndUpdate();

private:
  DrivingModeButton* stockADASButton;
  DrivingModeButton* chillButton;
  DrivingModeButton* experimentalButton;
  Params params;
  QTimer* updateTimer;
};
