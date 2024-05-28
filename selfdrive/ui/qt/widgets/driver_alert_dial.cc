#include "selfdrive/ui/qt/widgets/driver_alert_dial.h"
#include <algorithm>
// #include <QPainter>
// #include <QColor>
// #include <QBrush>

// This initializes the widgets properties
DriverAlertDial::DriverAlertDial(QWidget *parent) : QWidget(parent), confidence(0.0), steering_torque(0.0), brake_pressure(0.0), acceleration(0.0) {
  //Set the widget size
  setFixedSize(350, 350);
}

// Updates the internal state of widget
// add back in float conf,
void DriverAlertDial::updateState(float steer_torque, float brake, float accel) {
  // confidence = conf;
  steering_torque = steer_torque;
  brake_pressure = brake;
  acceleration = accel;
  update(); //this requests a repaint
}

void DriverAlertDial::paintEvent(QPaintEvent *event) {
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);

  // Draw the background circles
  // painter.setBrush(QColor(200, 200, 200));
  // painter.drawEllipse(10, 10, 180, 180);

  painter.setBrush(QBrush(Qt::red));
  painter.drawEllipse(QPointF(width()/2, height()/2), 175, 175);

  painter.setBrush(QBrush(Qt::yellow));
  painter.drawEllipse(QPointF(width()/2, height()/2), 120, 120);

  painter.setBrush(QBrush(Qt::cyan));
  painter.drawEllipse(QPointF(width()/2, height()/2), 55, 55);



  // Draw the alert ball
  QColor alert_color = getAlertColor(confidence);
  painter.setBrush(alert_color);
  QPointF ball_pos = calculateAlertBallPosition();
  painter.drawEllipse(ball_pos, 20, 20); // edit the ball size
}

// Determines what color alert ball is
QColor DriverAlertDial::getAlertColor(float conf) {
  if (confidence < 0.33) {
    return QColor(0, 255, 255); // Cyan
  } else if (confidence < 0.66) {
    return QColor(255, 255, 0); // Yellow
  } else {
    return QColor(255, 0, 0); // Red
  }
}

// MODIFY
QPointF DriverAlertDial::calculateAlertBallPosition() {
  float x = width() / 2;
  float y = height() / 2;

  // Define thresholds for different states
  float too_fast_threshold = 0.75;
  float too_slow_threshold = 0.25;
  float low_torque_threshold = -0.5;
  float high_torque_threshold = 0.5;

  // Scaling factor to control movement based on confidence
  float ball_movement_scale = (1.0f - confidence) * 100; //Higher confidence means alert ball moves less and stays in middle

  if (confidence < 0.66) {
    if (acceleration > too_fast_threshold) {
      // Too fast, move the ball up
      y -= (acceleration - too_fast_threshold) * ball_movement_scale;
    } else if (acceleration < too_slow_threshold) {
      // Too slow, move the ball up
      y += (too_slow_threshold - acceleration) * ball_movement_scale;
    }

    if (steering_torque < low_torque_threshold) {
      // Not enough torque, turn left
      x -= (low_torque_threshold - steering_torque) * ball_movement_scale;
    } else if (steering_torque > high_torque_threshold) {
      // Not enough torque, turn right
      x += (steering_torque - high_torque_threshold) * ball_movement_scale;
    }
  }

  // Clamp the position within the widget bounds
  x = std::max(10.0f, std::min(x, width() - 10.0f));
  y = std::max(10.0f, std::min(y, height() - 10.0f));

  return QPointF(x, y);
}
