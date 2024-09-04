#pragma once

#include <QWidget>
#include <QVBoxLayout>
#include "selfdrive/ui/qt/widgets/driving_mode_button.h"

class DrivingModePanel : public QWidget {
  Q_OBJECT

public:
  DrivingModePanel(QWidget* parent = nullptr);

private slots:
  void updateButtons();

private:
  DrivingModeButton* stockADASButton;
  DrivingModeButton* chillButton;
  DrivingModeButton* experimentalButton;
};
