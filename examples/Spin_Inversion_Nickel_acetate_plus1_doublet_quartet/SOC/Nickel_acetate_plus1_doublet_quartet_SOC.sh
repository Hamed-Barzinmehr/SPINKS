#!/bin/bash
#PBS -N Nickel_INT2_SOC
#PBS -l nodes=1:ppn=12
#PBS -o Nickel_INT2_SOC.pbs.out
#PBS -e Nickel_INT2_SOC.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Nickel_INT2_SOC"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Nickel_INT2_SOC.inp > Nickel_INT2_SOC.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
