#pragma once

#include <QLabel>
#include <QTimer>
#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"

class InfoDisplayBar : public QLabel {
  Q_OBJECT

public:
  explicit InfoDisplayBar(QWidget *parent = nullptr);

public slots:
  void showModeMessage(DrivingMode mode);

private:
  const QString DEFAULT_MESSAGE = "Tap mode for more info";
  QTimer *resetTimer;

  void resetToDefaultMessage();
};
