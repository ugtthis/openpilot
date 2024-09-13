#pragma once

#include <QPushButton>
#include <QLabel>
#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"

class DrivingModeButton : public QPushButton {
  Q_OBJECT

public:
  DrivingModeButton(const QString &text, DrivingMode mode, Params &params, QWidget *parent = nullptr);

  void updateState();

  DrivingMode getMode() const { return mode; }

private slots:
  void onClicked();

signals:
  void drivingModeChanged();

private:
  DrivingMode mode;
  Params &params;
  QLabel *statusCircle;  // Add this line
  QLabel *iconLabel;

  void updateCircle();
};
