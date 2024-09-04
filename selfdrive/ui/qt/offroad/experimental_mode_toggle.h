#pragma once

#include <QPushButton>
#include <QLabel>

#include "common/params.h"

class ExperimentalModeToggle : public QPushButton {
  Q_OBJECT

public:
  explicit ExperimentalModeToggle(QWidget *parent = nullptr);

private:
  Params params;
  bool experimental_mode = false;
  QLabel *mode_label;  // Add this line

  void paintEvent(QPaintEvent *event) override;
  void updateState();

private slots:
  void toggleExperimentalMode();
};