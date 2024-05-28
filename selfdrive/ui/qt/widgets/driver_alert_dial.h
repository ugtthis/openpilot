#pragma once

#include <QWidget>
#include <QPainter>
#include <QTimer>

class DADWidget : public QWidget {
  Q_OBJECT

public:
  explicit DADWidget(QWidget *parent = 0);
  void updateState(float confidence, float steering_torque, float brake_preassure, float acceleration);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  float confidence;
  float steering_torque;
  float brake_preassure;
  float acceleration;
  QColor getAlertColor(float confidence);
  QPointF calculateAlertBallPosition();
};